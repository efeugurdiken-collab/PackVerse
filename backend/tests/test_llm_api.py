"""Tests for the LLM Gateway API (Sprint P5): /api/v1/llm/*.

Every test here routes through the "fake" provider only - the app's
FastAPI dependency overrides (get_settings, get_llm_gateway) are
replaced with an isolated Settings instance and a gateway wrapping only
FakeProvider, so no test in this file can ever attempt a real network
call to Anthropic or OpenAI, regardless of what's in the real .env this
suite happens to run against.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.llm.exceptions import LLMRateLimitError, LLMTimeoutError
from app.llm.factory import get_llm_gateway
from app.llm.gateway import LLMGateway
from app.llm.models import ToolCall
from app.llm.providers.fake import FakeProvider
from app.main import app
from app.models.enums import UserRole
from app.models.llm_request import LLMRequestRecord

BASE = "/api/v1/llm"


def _llm_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "jwt_secret_key": "x" * 32,
        "llm_allowed_providers": "fake",
        "llm_default_provider": "fake",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _override(settings: Settings, provider: FakeProvider) -> None:
    # retry_base_delay_seconds=0.0: these tests assert on the final
    # mapped status code, not retry timing (that's covered by
    # tests/test_llm_gateway.py) - no need to actually sleep through
    # LLM_MAX_RETRIES backoff delays here.
    gateway = LLMGateway({"fake": provider}, settings, retry_base_delay_seconds=0.0)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_llm_gateway] = lambda: gateway


@pytest.fixture
def llm_gateway_override(client):
    """Default, always-succeeding fake gateway - the shared case for
    tests that don't care about a specific failure mode."""
    settings = _llm_settings()
    _override(settings, FakeProvider())
    yield settings


def _payload(**overrides: object) -> dict:
    payload = {
        "provider": "fake",
        "model": "fake-v1",
        "messages": [{"role": "user", "content": "hello"}],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
async def operator_headers(make_user, auth_headers) -> dict[str, str]:
    user = await make_user(role=UserRole.OPERATOR)
    return auth_headers(user)


@pytest.fixture
async def admin_headers(make_user, auth_headers) -> dict[str, str]:
    user = await make_user(role=UserRole.ADMIN)
    return auth_headers(user)


@pytest.fixture
async def viewer_headers(make_user, auth_headers) -> dict[str, str]:
    user = await make_user(role=UserRole.VIEWER)
    return auth_headers(user)


# --- Generate: authorization matrix ----------------------------------------


async def test_unauthenticated_generate_returns_401(client, llm_gateway_override) -> None:
    response = await client.post(f"{BASE}/generate", json=_payload())
    assert response.status_code == 401


async def test_viewer_generate_returns_403(client, llm_gateway_override, viewer_headers) -> None:
    response = await client.post(f"{BASE}/generate", json=_payload(), headers=viewer_headers)
    assert response.status_code == 403


async def test_operator_generate_succeeds(client, llm_gateway_override, operator_headers) -> None:
    response = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "fake"
    assert body["content"]


async def test_admin_generate_succeeds(client, llm_gateway_override, admin_headers) -> None:
    response = await client.post(f"{BASE}/generate", json=_payload(), headers=admin_headers)
    assert response.status_code == 200


async def test_generate_rejects_unsupported_message_role(
    client, llm_gateway_override, operator_headers
) -> None:
    response = await client.post(
        f"{BASE}/generate",
        json=_payload(messages=[{"role": "narrator", "content": "hi"}]),
        headers=operator_headers,
    )
    assert response.status_code == 422


# --- Generate: tool calling (Sprint P9A) --------------------------------


async def test_generate_response_has_null_tool_calls_by_default(
    client, llm_gateway_override, operator_headers
) -> None:
    response = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)

    assert response.status_code == 200
    assert response.json()["tool_calls"] is None


async def test_generate_with_tools_returns_tool_calls(client, operator_headers) -> None:
    settings = _llm_settings()
    tool_call = ToolCall(id="call_1", name="get_weather", arguments={"city": "nyc"})
    _override(settings, FakeProvider(tool_calls=(tool_call,)))

    response = await client.post(
        f"{BASE}/generate",
        json=_payload(
            tools=[
                {
                    "name": "get_weather",
                    "description": "Look up the current weather for a city",
                    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
                }
            ]
        ),
        headers=operator_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["finish_reason"] == "tool_use"
    assert body["tool_calls"] == [{"id": "call_1", "name": "get_weather", "arguments": {"city": "nyc"}}]


# --- Generate: error mapping -------------------------------------------


async def test_generate_maps_rate_limit_to_429(client, operator_headers) -> None:
    settings = _llm_settings()
    _override(settings, FakeProvider(fail_with=LLMRateLimitError("fake")))

    response = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)

    assert response.status_code == 429


async def test_generate_maps_timeout_to_504(client, operator_headers) -> None:
    settings = _llm_settings()
    _override(settings, FakeProvider(fail_with=LLMTimeoutError("fake")))

    response = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)

    assert response.status_code == 504


async def test_generate_with_unconfigured_provider_returns_503(client, operator_headers) -> None:
    settings = _llm_settings(llm_allowed_providers="fake,anthropic", llm_default_provider="fake")
    _override(settings, FakeProvider())

    response = await client.post(
        f"{BASE}/generate", json=_payload(provider="anthropic"), headers=operator_headers
    )

    assert response.status_code == 503


# --- Providers / models / health ------------------------------------------


async def test_provider_list(client, llm_gateway_override, viewer_headers) -> None:
    response = await client.get(f"{BASE}/providers", headers=viewer_headers)
    assert response.status_code == 200
    names = {p["name"] for p in response.json()}
    assert "fake" in names
    fake_entry = next(p for p in response.json() if p["name"] == "fake")
    assert fake_entry["configured"] is True


async def test_model_list(client, llm_gateway_override, viewer_headers) -> None:
    response = await client.get(f"{BASE}/models", headers=viewer_headers)
    assert response.status_code == 200
    body = response.json()
    assert "providers" in body
    assert "aliases" in body


async def test_health_endpoint(client, llm_gateway_override, viewer_headers) -> None:
    response = await client.get(f"{BASE}/health", headers=viewer_headers)
    assert response.status_code == 200
    statuses = {entry["provider"]: entry["status"] for entry in response.json()}
    assert statuses["fake"] == "reachable"


async def test_providers_models_health_require_authentication(client, llm_gateway_override) -> None:
    for path in ("providers", "models", "health"):
        response = await client.get(f"{BASE}/{path}")
        assert response.status_code == 401


# --- Request metadata retrieval ----------------------------------------


async def test_request_metadata_retrieval_by_owner(
    client, llm_gateway_override, operator_headers
) -> None:
    created = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)
    request_id = created.json()["request_id"]

    response = await client.get(f"{BASE}/requests/{request_id}", headers=operator_headers)

    assert response.status_code == 200
    assert response.json()["id"] == request_id
    assert response.json()["status"] == "succeeded"


async def test_unknown_request_returns_404(client, llm_gateway_override, operator_headers) -> None:
    response = await client.get(f"{BASE}/requests/{uuid.uuid4()}", headers=operator_headers)
    assert response.status_code == 404


async def test_non_admin_cannot_view_another_users_request(
    client, llm_gateway_override, operator_headers, viewer_headers
) -> None:
    created = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)
    request_id = created.json()["request_id"]

    response = await client.get(f"{BASE}/requests/{request_id}", headers=viewer_headers)

    assert response.status_code == 404  # not 403 - avoids confirming the id exists


async def test_admin_can_view_any_users_request(
    client, llm_gateway_override, operator_headers, admin_headers
) -> None:
    created = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)
    request_id = created.json()["request_id"]

    response = await client.get(f"{BASE}/requests/{request_id}", headers=admin_headers)

    assert response.status_code == 200


async def test_request_metadata_never_includes_prompt_content(
    client, llm_gateway_override, operator_headers
) -> None:
    created = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)
    request_id = created.json()["request_id"]

    response = await client.get(f"{BASE}/requests/{request_id}", headers=operator_headers)

    body = response.json()
    assert "content" not in body
    assert "messages" not in body
    assert "prompt" not in body


# --- Persistence ---------------------------------------------------------


async def test_successful_request_is_persisted(
    client, llm_gateway_override, operator_headers, db_session
) -> None:
    created = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)
    request_id = uuid.UUID(created.json()["request_id"])

    record = await db_session.get(LLMRequestRecord, request_id)
    assert record is not None
    assert record.status.value == "succeeded"


async def test_failed_request_is_persisted(
    client, operator_headers, db_session
) -> None:
    settings = _llm_settings()
    _override(settings, FakeProvider(fail_with=LLMTimeoutError("fake")))

    response = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)
    assert response.status_code == 504

    result = await db_session.execute(
        select(LLMRequestRecord).where(LLMRequestRecord.provider == "fake")
    )
    records = result.scalars().all()
    assert any(r.status.value == "failed" and r.error_code == "LLMTimeoutError" for r in records)


async def test_token_counts_are_stored(
    client, llm_gateway_override, operator_headers, db_session
) -> None:
    created = await client.post(
        f"{BASE}/generate",
        json=_payload(messages=[{"role": "user", "content": "one two three four"}]),
        headers=operator_headers,
    )
    request_id = uuid.UUID(created.json()["request_id"])

    record = await db_session.get(LLMRequestRecord, request_id)
    assert record.input_tokens == 4
    assert record.output_tokens is not None
    assert record.total_tokens == record.input_tokens + record.output_tokens


async def test_latency_is_stored(
    client, llm_gateway_override, operator_headers, db_session
) -> None:
    created = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)
    request_id = uuid.UUID(created.json()["request_id"])

    record = await db_session.get(LLMRequestRecord, request_id)
    assert record.latency_ms is not None
    assert record.latency_ms >= 0


async def test_cost_is_stored_when_pricing_is_configured(
    client, operator_headers, db_session
) -> None:
    settings = _llm_settings(
        llm_pricing_json='{"fake:fake-v1": {"input_per_1k": "1.00", "output_per_1k": "2.00"}}'
    )
    _override(settings, FakeProvider())

    created = await client.post(f"{BASE}/generate", json=_payload(), headers=operator_headers)
    request_id = uuid.UUID(created.json()["request_id"])

    record = await db_session.get(LLMRequestRecord, request_id)
    assert record.estimated_cost_usd is not None
    assert record.estimated_cost_usd > 0


async def test_prompt_content_is_not_persisted_by_default(
    client, llm_gateway_override, operator_headers, db_session
) -> None:
    created = await client.post(
        f"{BASE}/generate",
        json=_payload(messages=[{"role": "user", "content": "a very secret prompt"}]),
        headers=operator_headers,
    )
    request_id = uuid.UUID(created.json()["request_id"])

    record = await db_session.get(LLMRequestRecord, request_id)
    # No column on the model holds prompt/response text at all - see
    # app/models/llm_request.py - so there is nothing to assert equal to
    # empty; this asserts the row's own metadata blobs don't contain it
    # either (defense in depth, since request_metadata_json only ever
    # receives the client-supplied `metadata` field, never `messages`).
    assert "a very secret prompt" not in str(record.request_metadata_json)
    assert "a very secret prompt" not in str(record.response_metadata_json)

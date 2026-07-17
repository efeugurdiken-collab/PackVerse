"""Tests for the Agent Run API (Sprint P6): /api/v1/runs/*.

Every test routes through the network-free "fake" LLM provider - the
app's FastAPI dependency overrides (get_settings, get_llm_gateway) are
replaced with an isolated Settings instance and a gateway wrapping only
FakeProvider, exactly mirroring tests/test_llm_api.py's approach, so no
test here can ever attempt a real network call.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings, get_settings
from app.llm.exceptions import LLMRateLimitError, LLMTimeoutError
from app.llm.factory import get_llm_gateway
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeProvider
from app.main import app
from app.models.enums import AgentStatus, UserRole

BASE = "/api/v1/runs"


def _runtime_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "jwt_secret_key": "x" * 32,
        "llm_allowed_providers": "fake",
        "llm_default_provider": "fake",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _override(settings: Settings, provider: FakeProvider) -> None:
    gateway = LLMGateway({"fake": provider}, settings, retry_base_delay_seconds=0.0)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_llm_gateway] = lambda: gateway


@pytest.fixture
def runtime_gateway_override(client):
    """Default, always-succeeding fake gateway - the shared case for
    tests that don't care about a specific failure mode."""
    settings = _runtime_settings()
    _override(settings, FakeProvider())
    yield settings


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


def _payload(agent_id: uuid.UUID, **overrides: object) -> dict:
    payload = {"agent_id": str(agent_id), "user_input": "hello"}
    payload.update(overrides)
    return payload


# --- Create+execute: authorization matrix --------------------------------


async def test_unauthenticated_create_run_returns_401(
    client, runtime_gateway_override, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    response = await client.post(BASE, json=_payload(agent.id))
    assert response.status_code == 401


async def test_viewer_create_run_returns_403(
    client, runtime_gateway_override, viewer_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    response = await client.post(BASE, json=_payload(agent.id), headers=viewer_headers)
    assert response.status_code == 403


async def test_operator_create_run_succeeds(
    client, runtime_gateway_override, operator_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    response = await client.post(BASE, json=_payload(agent.id), headers=operator_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["output_text"]


async def test_admin_create_run_succeeds(
    client, runtime_gateway_override, admin_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    response = await client.post(BASE, json=_payload(agent.id), headers=admin_headers)
    assert response.status_code == 201


async def test_create_run_rejects_empty_user_input(
    client, runtime_gateway_override, operator_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    response = await client.post(
        BASE, json=_payload(agent.id, user_input=""), headers=operator_headers
    )
    assert response.status_code == 422


# --- Create+execute: error mapping ----------------------------------------


async def test_create_run_with_unknown_agent_returns_404(
    client, runtime_gateway_override, operator_headers
) -> None:
    response = await client.post(BASE, json=_payload(uuid.uuid4()), headers=operator_headers)
    assert response.status_code == 404


async def test_create_run_with_draft_agent_returns_409(
    client, runtime_gateway_override, operator_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition(status=AgentStatus.DRAFT)
    response = await client.post(BASE, json=_payload(agent.id), headers=operator_headers)
    assert response.status_code == 409


async def test_create_run_maps_rate_limit_to_429(
    client, operator_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    settings = _runtime_settings()
    _override(settings, FakeProvider(fail_with=LLMRateLimitError("fake")))

    response = await client.post(BASE, json=_payload(agent.id), headers=operator_headers)

    assert response.status_code == 429


async def test_create_run_maps_timeout_to_504(
    client, operator_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    settings = _runtime_settings()
    _override(settings, FakeProvider(fail_with=LLMTimeoutError("fake")))

    response = await client.post(BASE, json=_payload(agent.id), headers=operator_headers)

    assert response.status_code == 504


async def test_create_run_with_misconfigured_agent_returns_422(
    client, runtime_gateway_override, operator_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition(configuration_json={"model": "fake-v1"})
    response = await client.post(BASE, json=_payload(agent.id), headers=operator_headers)
    assert response.status_code == 422


# --- Retrieval -------------------------------------------------------------


async def test_owner_can_retrieve_own_run(
    client, runtime_gateway_override, operator_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    created = await client.post(BASE, json=_payload(agent.id), headers=operator_headers)
    run_id = created.json()["id"]

    response = await client.get(f"{BASE}/{run_id}", headers=operator_headers)

    assert response.status_code == 200
    assert response.json()["id"] == run_id


async def test_unknown_run_returns_404(
    client, runtime_gateway_override, operator_headers
) -> None:
    response = await client.get(f"{BASE}/{uuid.uuid4()}", headers=operator_headers)
    assert response.status_code == 404


async def test_non_owner_cannot_view_another_users_run(
    client, runtime_gateway_override, operator_headers, viewer_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    created = await client.post(BASE, json=_payload(agent.id), headers=operator_headers)
    run_id = created.json()["id"]

    response = await client.get(f"{BASE}/{run_id}", headers=viewer_headers)

    assert response.status_code == 404  # not 403 - avoids confirming the id exists


async def test_admin_can_view_any_users_run(
    client, runtime_gateway_override, operator_headers, admin_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    created = await client.post(BASE, json=_payload(agent.id), headers=operator_headers)
    run_id = created.json()["id"]

    response = await client.get(f"{BASE}/{run_id}", headers=admin_headers)

    assert response.status_code == 200


async def test_run_read_never_includes_user_input_or_context(
    client, runtime_gateway_override, operator_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    created = await client.post(
        BASE,
        json=_payload(agent.id, user_input="a very secret prompt"),
        headers=operator_headers,
    )
    body = created.json()

    assert "user_input" not in body
    assert "context" not in body


async def test_list_runs_requires_authentication(client, runtime_gateway_override) -> None:
    response = await client.get(BASE)
    assert response.status_code == 401


async def test_list_runs_scopes_to_own_for_non_admin(
    client, runtime_gateway_override, operator_headers, admin_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    await client.post(BASE, json=_payload(agent.id), headers=operator_headers)
    await client.post(BASE, json=_payload(agent.id), headers=admin_headers)

    response = await client.get(BASE, headers=operator_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1


async def test_list_runs_returns_all_for_admin(
    client, runtime_gateway_override, operator_headers, admin_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    await client.post(BASE, json=_payload(agent.id), headers=operator_headers)
    await client.post(BASE, json=_payload(agent.id), headers=admin_headers)

    response = await client.get(BASE, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 2


# --- Cancellation ------------------------------------------------------


async def test_cancel_requires_authentication(client, runtime_gateway_override) -> None:
    response = await client.post(f"{BASE}/{uuid.uuid4()}/cancel")
    assert response.status_code == 401


async def test_viewer_cannot_cancel(
    client, runtime_gateway_override, viewer_headers
) -> None:
    response = await client.post(f"{BASE}/{uuid.uuid4()}/cancel", headers=viewer_headers)
    assert response.status_code == 403


async def test_cancel_unknown_run_returns_404(
    client, runtime_gateway_override, operator_headers
) -> None:
    response = await client.post(f"{BASE}/{uuid.uuid4()}/cancel", headers=operator_headers)
    assert response.status_code == 404


async def test_cancel_already_completed_run_returns_409(
    client, runtime_gateway_override, operator_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    created = await client.post(BASE, json=_payload(agent.id), headers=operator_headers)
    run_id = created.json()["id"]
    assert created.json()["status"] == "completed"

    response = await client.post(f"{BASE}/{run_id}/cancel", headers=operator_headers)

    assert response.status_code == 409


async def test_non_owner_cannot_cancel_someone_elses_run(
    client, runtime_gateway_override, operator_headers, admin_headers, make_agent_definition
) -> None:
    agent = await make_agent_definition()
    created = await client.post(BASE, json=_payload(agent.id), headers=admin_headers)
    run_id = created.json()["id"]

    response = await client.post(f"{BASE}/{run_id}/cancel", headers=operator_headers)

    assert response.status_code == 404

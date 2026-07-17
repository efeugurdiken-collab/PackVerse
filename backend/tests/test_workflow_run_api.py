"""Tests for the Workflow Run API (Sprint P7): /api/v1/workflow-runs/*.

Every test routes through the network-free "fake" LLM provider - the
app's FastAPI dependency overrides (get_settings, get_llm_gateway) are
replaced with an isolated Settings instance and a gateway wrapping only
FakeProvider, exactly mirroring tests/test_runtime_api.py's approach.
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
from app.models.enums import AgentStatus, UserRole, WorkflowStatus

BASE = "/api/v1/workflow-runs"


def _workflow_settings(**overrides: object) -> Settings:
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
def workflow_gateway_override(client):
    """Default, always-succeeding fake gateway - the shared case for
    tests that don't care about a specific failure mode."""
    settings = _workflow_settings()
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


def _payload(workflow_id: uuid.UUID, **overrides: object) -> dict:
    payload = {"workflow_id": str(workflow_id), "user_input": "hello"}
    payload.update(overrides)
    return payload


async def _one_step_workflow(make_agent_definition, make_workflow_definition, **overrides):
    agent = await make_agent_definition()
    steps = [{"step_id": "only", "name": "Only", "agent_definition_id": str(agent.id), "order": 1}]
    return await make_workflow_definition(steps=steps, **overrides)


# --- Create+execute: authorization matrix --------------------------------


async def test_unauthenticated_create_run_returns_401(
    client, workflow_gateway_override, make_agent_definition, make_workflow_definition
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    response = await client.post(BASE, json=_payload(workflow.id))
    assert response.status_code == 401


async def test_viewer_create_run_returns_403(
    client,
    workflow_gateway_override,
    viewer_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    response = await client.post(BASE, json=_payload(workflow.id), headers=viewer_headers)
    assert response.status_code == 403


async def test_operator_create_run_succeeds(
    client,
    workflow_gateway_override,
    operator_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    response = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["output_text"]


async def test_admin_create_run_succeeds(
    client,
    workflow_gateway_override,
    admin_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    response = await client.post(BASE, json=_payload(workflow.id), headers=admin_headers)
    assert response.status_code == 201


async def test_create_run_rejects_empty_user_input(
    client,
    workflow_gateway_override,
    operator_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    response = await client.post(
        BASE, json=_payload(workflow.id, user_input=""), headers=operator_headers
    )
    assert response.status_code == 422


# --- Create+execute: error mapping ----------------------------------------


async def test_create_run_with_unknown_workflow_returns_404(
    client, workflow_gateway_override, operator_headers
) -> None:
    response = await client.post(BASE, json=_payload(uuid.uuid4()), headers=operator_headers)
    assert response.status_code == 404


async def test_create_run_with_draft_workflow_returns_409(
    client,
    workflow_gateway_override,
    operator_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(
        make_agent_definition, make_workflow_definition, status=WorkflowStatus.DRAFT
    )
    response = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    assert response.status_code == 409


async def test_create_run_with_empty_steps_returns_422(
    client, workflow_gateway_override, operator_headers, make_workflow_definition
) -> None:
    workflow = await make_workflow_definition(steps=[])
    response = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    assert response.status_code == 422


async def test_create_run_with_unknown_agent_returns_404(
    client, workflow_gateway_override, operator_headers, make_workflow_definition
) -> None:
    steps = [{"step_id": "a", "name": "A", "agent_definition_id": str(uuid.uuid4()), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)
    response = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    assert response.status_code == 404


async def test_create_run_with_inactive_agent_returns_409(
    client,
    workflow_gateway_override,
    operator_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    agent = await make_agent_definition(status=AgentStatus.DRAFT)
    steps = [{"step_id": "a", "name": "A", "agent_definition_id": str(agent.id), "order": 1}]
    workflow = await make_workflow_definition(steps=steps)
    response = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    assert response.status_code == 409


async def test_create_run_maps_rate_limit_to_429(
    client, operator_headers, make_agent_definition, make_workflow_definition
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    settings = _workflow_settings()
    _override(settings, FakeProvider(fail_with=LLMRateLimitError("fake")))

    response = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)

    assert response.status_code == 429


async def test_create_run_maps_timeout_to_504(
    client, operator_headers, make_agent_definition, make_workflow_definition
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    settings = _workflow_settings()
    _override(settings, FakeProvider(fail_with=LLMTimeoutError("fake")))

    response = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)

    assert response.status_code == 504


# --- Retrieval -------------------------------------------------------------


async def test_owner_can_retrieve_own_run(
    client,
    workflow_gateway_override,
    operator_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    created = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    run_id = created.json()["id"]

    response = await client.get(f"{BASE}/{run_id}", headers=operator_headers)

    assert response.status_code == 200
    assert response.json()["id"] == run_id


async def test_unknown_run_returns_404(
    client, workflow_gateway_override, operator_headers
) -> None:
    response = await client.get(f"{BASE}/{uuid.uuid4()}", headers=operator_headers)
    assert response.status_code == 404


async def test_non_owner_cannot_view_another_users_run(
    client,
    workflow_gateway_override,
    operator_headers,
    viewer_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    created = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    run_id = created.json()["id"]

    response = await client.get(f"{BASE}/{run_id}", headers=viewer_headers)

    assert response.status_code == 404  # not 403 - avoids confirming the id exists


async def test_admin_can_view_any_users_run(
    client,
    workflow_gateway_override,
    operator_headers,
    admin_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    created = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    run_id = created.json()["id"]

    response = await client.get(f"{BASE}/{run_id}", headers=admin_headers)

    assert response.status_code == 200


async def test_run_read_never_includes_user_input_or_context(
    client,
    workflow_gateway_override,
    operator_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    created = await client.post(
        BASE,
        json=_payload(workflow.id, user_input="a very secret prompt"),
        headers=operator_headers,
    )
    body = created.json()

    assert "user_input" not in body
    assert "context" not in body


async def test_list_runs_requires_authentication(client, workflow_gateway_override) -> None:
    response = await client.get(BASE)
    assert response.status_code == 401


async def test_list_runs_scopes_to_own_for_non_admin(
    client,
    workflow_gateway_override,
    operator_headers,
    admin_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    await client.post(BASE, json=_payload(workflow.id), headers=admin_headers)

    response = await client.get(BASE, headers=operator_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_list_runs_returns_all_for_admin(
    client,
    workflow_gateway_override,
    operator_headers,
    admin_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    await client.post(BASE, json=_payload(workflow.id), headers=admin_headers)

    response = await client.get(BASE, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 2


# --- Step listing ------------------------------------------------------


async def test_owner_can_list_steps_of_own_run(
    client,
    workflow_gateway_override,
    operator_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    created = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    run_id = created.json()["id"]

    response = await client.get(f"{BASE}/{run_id}/steps", headers=operator_headers)

    assert response.status_code == 200
    steps = response.json()
    assert len(steps) == 1
    assert steps[0]["status"] == "completed"
    assert steps[0]["output_text"]


async def test_list_steps_for_unknown_run_returns_404(
    client, workflow_gateway_override, operator_headers
) -> None:
    response = await client.get(f"{BASE}/{uuid.uuid4()}/steps", headers=operator_headers)
    assert response.status_code == 404


async def test_non_owner_cannot_list_steps_of_someone_elses_run(
    client,
    workflow_gateway_override,
    operator_headers,
    viewer_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    created = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    run_id = created.json()["id"]

    response = await client.get(f"{BASE}/{run_id}/steps", headers=viewer_headers)

    assert response.status_code == 404


# --- Cancellation ------------------------------------------------------


async def test_cancel_requires_authentication(client, workflow_gateway_override) -> None:
    response = await client.post(f"{BASE}/{uuid.uuid4()}/cancel")
    assert response.status_code == 401


async def test_viewer_cannot_cancel(client, workflow_gateway_override, viewer_headers) -> None:
    response = await client.post(f"{BASE}/{uuid.uuid4()}/cancel", headers=viewer_headers)
    assert response.status_code == 403


async def test_cancel_unknown_run_returns_404(
    client, workflow_gateway_override, operator_headers
) -> None:
    response = await client.post(f"{BASE}/{uuid.uuid4()}/cancel", headers=operator_headers)
    assert response.status_code == 404


async def test_cancel_already_completed_run_returns_409(
    client,
    workflow_gateway_override,
    operator_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    created = await client.post(BASE, json=_payload(workflow.id), headers=operator_headers)
    run_id = created.json()["id"]
    assert created.json()["status"] == "completed"

    response = await client.post(f"{BASE}/{run_id}/cancel", headers=operator_headers)

    assert response.status_code == 409


async def test_non_owner_cannot_cancel_someone_elses_run(
    client,
    workflow_gateway_override,
    operator_headers,
    admin_headers,
    make_agent_definition,
    make_workflow_definition,
) -> None:
    workflow = await _one_step_workflow(make_agent_definition, make_workflow_definition)
    created = await client.post(BASE, json=_payload(workflow.id), headers=admin_headers)
    run_id = created.json()["id"]

    response = await client.post(f"{BASE}/{run_id}/cancel", headers=operator_headers)

    assert response.status_code == 404

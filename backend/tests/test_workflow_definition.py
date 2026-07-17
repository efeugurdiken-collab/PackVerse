"""Tests for app/workflows/definition.py's parse_workflow_steps and the
InputMapping vocabulary it enforces (Sprint P7). Pure, DB-free - every
agent_definition_id here is a freshly-generated uuid4, never persisted;
this module never checks that an agent actually exists (that is
app/workflows/service.py's job, exercised separately in
tests/test_workflow_service.py)."""
from __future__ import annotations

import uuid

import pytest

from app.workflows.definition import InputMapping, parse_workflow_steps
from app.workflows.exceptions import WorkflowDefinitionInvalidError


def _agent_id() -> str:
    return str(uuid.uuid4())


def test_valid_two_step_workflow_parses_in_order() -> None:
    a1, a2 = _agent_id(), _agent_id()
    steps = parse_workflow_steps(
        "wf",
        {
            "steps": [
                {"step_id": "research", "name": "Research", "agent_definition_id": a1, "order": 1},
                {
                    "step_id": "summarize",
                    "name": "Summarize",
                    "agent_definition_id": a2,
                    "order": 2,
                },
            ]
        },
    )
    assert [s.step_id for s in steps] == ["research", "summarize"]
    assert steps[0].input_mapping == InputMapping(source="workflow_input")
    assert steps[1].input_mapping == InputMapping(source="previous_output")


def test_steps_given_out_of_order_are_sorted_by_order_field() -> None:
    a1, a2 = _agent_id(), _agent_id()
    steps = parse_workflow_steps(
        "wf",
        {
            "steps": [
                {"step_id": "b", "name": "B", "agent_definition_id": a1, "order": 5},
                {"step_id": "a", "name": "A", "agent_definition_id": a2, "order": 1},
            ]
        },
    )
    assert [s.step_id for s in steps] == ["a", "b"]
    # Default resolution follows the sorted (execution) order, not the
    # order the steps appeared in the input list.
    assert steps[0].input_mapping.source == "workflow_input"
    assert steps[1].input_mapping.source == "previous_output"


def test_explicit_step_output_mapping_to_an_earlier_step() -> None:
    a1, a2, a3 = _agent_id(), _agent_id(), _agent_id()
    steps = parse_workflow_steps(
        "wf",
        {
            "steps": [
                {"step_id": "research", "name": "Research", "agent_definition_id": a1, "order": 1},
                {
                    "step_id": "summarize",
                    "name": "Summarize",
                    "agent_definition_id": a2,
                    "order": 2,
                },
                {
                    "step_id": "translate",
                    "name": "Translate",
                    "agent_definition_id": a3,
                    "order": 3,
                    "input_mapping": {"source": "step_output", "step_id": "research"},
                },
            ]
        },
    )
    assert steps[2].input_mapping == InputMapping(source="step_output", step_id="research")


def test_explicit_static_mapping() -> None:
    a1 = _agent_id()
    steps = parse_workflow_steps(
        "wf",
        {
            "steps": [
                {
                    "step_id": "a",
                    "name": "A",
                    "agent_definition_id": a1,
                    "order": 1,
                    "input_mapping": {"source": "static", "value": "fixed text"},
                }
            ]
        },
    )
    assert steps[0].input_mapping == InputMapping(source="static", value="fixed text")


def test_explicit_workflow_input_mapping_on_a_later_step() -> None:
    a1, a2 = _agent_id(), _agent_id()
    steps = parse_workflow_steps(
        "wf",
        {
            "steps": [
                {"step_id": "a", "name": "A", "agent_definition_id": a1, "order": 1},
                {
                    "step_id": "b",
                    "name": "B",
                    "agent_definition_id": a2,
                    "order": 2,
                    "input_mapping": {"source": "workflow_input"},
                },
            ]
        },
    )
    assert steps[1].input_mapping == InputMapping(source="workflow_input")


def test_empty_steps_rejected() -> None:
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps("wf", {"steps": []})


def test_missing_steps_key_rejected() -> None:
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps("wf", {})


def test_non_list_steps_rejected() -> None:
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps("wf", {"steps": "not-a-list"})


def test_duplicate_step_id_rejected() -> None:
    a1, a2 = _agent_id(), _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {"step_id": "a", "name": "A", "agent_definition_id": a1, "order": 1},
                    {"step_id": "a", "name": "A2", "agent_definition_id": a2, "order": 2},
                ]
            },
        )


def test_duplicate_order_rejected() -> None:
    a1, a2 = _agent_id(), _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {"step_id": "a", "name": "A", "agent_definition_id": a1, "order": 1},
                    {"step_id": "b", "name": "B", "agent_definition_id": a2, "order": 1},
                ]
            },
        )


def test_missing_agent_definition_id_rejected() -> None:
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf", {"steps": [{"step_id": "a", "name": "A", "order": 1}]}
        )


def test_malformed_agent_definition_id_rejected() -> None:
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {
                        "step_id": "a",
                        "name": "A",
                        "agent_definition_id": "not-a-uuid",
                        "order": 1,
                    }
                ]
            },
        )


def test_malformed_step_not_an_object_rejected() -> None:
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps("wf", {"steps": ["not-an-object"]})


def test_missing_step_id_rejected() -> None:
    a1 = _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf", {"steps": [{"name": "A", "agent_definition_id": a1, "order": 1}]}
        )


def test_missing_name_rejected() -> None:
    a1 = _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf", {"steps": [{"step_id": "a", "agent_definition_id": a1, "order": 1}]}
        )


def test_non_integer_order_rejected() -> None:
    a1 = _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {"steps": [{"step_id": "a", "name": "A", "agent_definition_id": a1, "order": "1"}]},
        )


def test_forward_reference_step_output_rejected() -> None:
    a1, a2 = _agent_id(), _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {
                        "step_id": "a",
                        "name": "A",
                        "agent_definition_id": a1,
                        "order": 1,
                        "input_mapping": {"source": "step_output", "step_id": "b"},
                    },
                    {"step_id": "b", "name": "B", "agent_definition_id": a2, "order": 2},
                ]
            },
        )


def test_self_reference_step_output_rejected() -> None:
    a1 = _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {
                        "step_id": "a",
                        "name": "A",
                        "agent_definition_id": a1,
                        "order": 1,
                        "input_mapping": {"source": "step_output", "step_id": "a"},
                    }
                ]
            },
        )


def test_step_output_missing_step_id_rejected() -> None:
    a1 = _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {
                        "step_id": "a",
                        "name": "A",
                        "agent_definition_id": a1,
                        "order": 1,
                        "input_mapping": {"source": "step_output"},
                    }
                ]
            },
        )


def test_static_mapping_missing_value_rejected() -> None:
    a1 = _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {
                        "step_id": "a",
                        "name": "A",
                        "agent_definition_id": a1,
                        "order": 1,
                        "input_mapping": {"source": "static"},
                    }
                ]
            },
        )


def test_unsupported_mapping_source_rejected() -> None:
    a1 = _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {
                        "step_id": "a",
                        "name": "A",
                        "agent_definition_id": a1,
                        "order": 1,
                        "input_mapping": {"source": "arbitrary_code"},
                    }
                ]
            },
        )


def test_input_mapping_not_an_object_rejected() -> None:
    a1 = _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {
                        "step_id": "a",
                        "name": "A",
                        "agent_definition_id": a1,
                        "order": 1,
                        "input_mapping": "workflow_input",
                    }
                ]
            },
        )


def test_previous_output_on_first_step_rejected() -> None:
    a1 = _agent_id()
    with pytest.raises(WorkflowDefinitionInvalidError):
        parse_workflow_steps(
            "wf",
            {
                "steps": [
                    {
                        "step_id": "a",
                        "name": "A",
                        "agent_definition_id": a1,
                        "order": 1,
                        "input_mapping": {"source": "previous_output"},
                    }
                ]
            },
        )

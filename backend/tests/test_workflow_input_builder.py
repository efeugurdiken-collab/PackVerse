"""Tests for app/workflows/input_builder.py's build_step_input (Sprint
P7). Pure, DB-free - uses app/workflows/definition.py's WorkflowStepSpec/
InputMapping directly rather than going through the database."""
from __future__ import annotations

import uuid

import pytest

from app.workflows.definition import InputMapping, WorkflowStepSpec
from app.workflows.exceptions import WorkflowInputResolutionError
from app.workflows.input_builder import build_step_input


def _spec(step_id: str, mapping: InputMapping) -> WorkflowStepSpec:
    return WorkflowStepSpec(
        step_id=step_id,
        name=step_id,
        agent_definition_id=uuid.uuid4(),
        order=1,
        input_mapping=mapping,
    )


def test_workflow_input_mapping_returns_original_user_input() -> None:
    spec = _spec("first", InputMapping(source="workflow_input"))
    result = build_step_input(
        spec, workflow_user_input="ORIGINAL", previous_step_id=None, step_outputs={}
    )
    assert result == "ORIGINAL"


def test_previous_output_mapping_returns_previous_steps_output() -> None:
    spec = _spec("second", InputMapping(source="previous_output"))
    result = build_step_input(
        spec,
        workflow_user_input="ORIGINAL",
        previous_step_id="first",
        step_outputs={"first": "FIRST OUTPUT"},
    )
    assert result == "FIRST OUTPUT"


def test_step_output_mapping_returns_named_earlier_steps_output() -> None:
    spec = _spec("third", InputMapping(source="step_output", step_id="first"))
    result = build_step_input(
        spec,
        workflow_user_input="ORIGINAL",
        previous_step_id="second",
        step_outputs={"first": "FIRST OUTPUT", "second": "SECOND OUTPUT"},
    )
    assert result == "FIRST OUTPUT"


def test_static_mapping_returns_fixed_value() -> None:
    spec = _spec("any", InputMapping(source="static", value="FIXED TEXT"))
    result = build_step_input(
        spec, workflow_user_input="ignored", previous_step_id=None, step_outputs={}
    )
    assert result == "FIXED TEXT"


def test_deterministic_output_for_the_same_inputs() -> None:
    spec = _spec("second", InputMapping(source="previous_output"))
    kwargs = dict(
        workflow_user_input="ORIGINAL", previous_step_id="first", step_outputs={"first": "X"}
    )
    assert build_step_input(spec, **kwargs) == build_step_input(spec, **kwargs)


def test_previous_output_with_no_preceding_step_raises() -> None:
    """Defensive path - app/workflows/definition.py already statically
    rejects this at parse time, but the input builder itself must not
    silently proceed if ever called this way directly."""
    spec = _spec("first", InputMapping(source="previous_output"))
    with pytest.raises(WorkflowInputResolutionError):
        build_step_input(
            spec, workflow_user_input="ORIGINAL", previous_step_id=None, step_outputs={}
        )


def test_previous_output_missing_from_step_outputs_raises() -> None:
    spec = _spec("second", InputMapping(source="previous_output"))
    with pytest.raises(WorkflowInputResolutionError):
        build_step_input(
            spec, workflow_user_input="ORIGINAL", previous_step_id="first", step_outputs={}
        )


def test_step_output_reference_missing_from_step_outputs_raises() -> None:
    spec = _spec("third", InputMapping(source="step_output", step_id="first"))
    with pytest.raises(WorkflowInputResolutionError):
        build_step_input(
            spec, workflow_user_input="ORIGINAL", previous_step_id="second", step_outputs={}
        )

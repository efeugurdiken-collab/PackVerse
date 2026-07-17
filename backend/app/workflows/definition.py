"""WorkflowDefinition.definition_json convention and validation (Sprint
P7). No AgentDefinition/WorkflowDefinition CRUD API was added by this
sprint - definitions are still seeded directly (same as
app/runtime/prompt_builder.py's AgentDefinition.configuration_json
convention) - so this module is the only place the "steps" shape is
enforced.

Convention for WorkflowDefinition.definition_json::

    {
      "steps": [
        {
          "step_id": "research",           # unique within this workflow
          "name": "Research",
          "agent_definition_id": "<uuid>",
          "order": 1,                       # unique, sets execution order
          "input_mapping": {"source": "workflow_input"}   # optional
        },
        {
          "step_id": "summarize",
          "name": "Summarize",
          "agent_definition_id": "<uuid>",
          "order": 2,
          "input_mapping": {"source": "previous_output"}  # optional
        }
      ]
    }

input_mapping is a small, closed, deterministic vocabulary - never a
general-purpose template language or arbitrary code, per the sprint's
explicit "No arbitrary template execution":

    {"source": "workflow_input"}                    - the run's own user_input
    {"source": "previous_output"}                    - the immediately preceding step's output
    {"source": "step_output", "step_id": "<id>"}     - a named EARLIER step's output
    {"source": "static", "value": "<text>"}          - a fixed string

If input_mapping is omitted: the first step (lowest order) defaults to
workflow_input; every later step defaults to previous_output.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from app.workflows.exceptions import WorkflowDefinitionInvalidError

_MAPPING_SOURCES = frozenset({"workflow_input", "previous_output", "step_output", "static"})


@dataclass(frozen=True)
class InputMapping:
    source: Literal["workflow_input", "previous_output", "step_output", "static"]
    step_id: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class WorkflowStepSpec:
    step_id: str
    name: str
    agent_definition_id: uuid.UUID
    order: int
    input_mapping: InputMapping


def _parse_input_mapping(
    workflow_id: object,
    step_id: str,
    raw: object,
    *,
    known_earlier_step_ids: set[str],
    is_first_step: bool,
) -> InputMapping:
    if not isinstance(raw, dict):
        raise WorkflowDefinitionInvalidError(
            workflow_id, f"step {step_id!r}: input_mapping must be an object"
        )
    source = raw.get("source")
    if source not in _MAPPING_SOURCES:
        raise WorkflowDefinitionInvalidError(
            workflow_id, f"step {step_id!r}: unsupported input_mapping source {source!r}"
        )
    if source == "previous_output" and is_first_step:
        # Statically rejected here (same spirit as the self-reference and
        # forward-reference checks below) rather than left to fail at
        # execution time in app/workflows/input_builder.py - the first
        # step never has a preceding step, so this is a definition
        # problem, not a runtime one.
        raise WorkflowDefinitionInvalidError(
            workflow_id, f"step {step_id!r}: previous_output mapping requires a preceding step"
        )
    if source == "step_output":
        ref = raw.get("step_id")
        if not isinstance(ref, str) or not ref:
            raise WorkflowDefinitionInvalidError(
                workflow_id, f"step {step_id!r}: step_output mapping requires a step_id"
            )
        if ref == step_id:
            raise WorkflowDefinitionInvalidError(
                workflow_id, f"step {step_id!r}: cannot reference its own output"
            )
        if ref not in known_earlier_step_ids:
            raise WorkflowDefinitionInvalidError(
                workflow_id,
                f"step {step_id!r}: step_output references {ref!r}, which is not an earlier step",
            )
        return InputMapping(source="step_output", step_id=ref)
    if source == "static":
        value = raw.get("value")
        if not isinstance(value, str):
            raise WorkflowDefinitionInvalidError(
                workflow_id, f"step {step_id!r}: static mapping requires a string value"
            )
        return InputMapping(source="static", value=value)
    if source == "workflow_input":
        return InputMapping(source="workflow_input")
    return InputMapping(source="previous_output")


def parse_workflow_steps(
    workflow_id: object, definition_json: dict[str, object]
) -> list[WorkflowStepSpec]:
    """Raises WorkflowDefinitionInvalidError for any structural problem.
    Does NOT check that each agent_definition_id exists/is active in the
    database - that is app/workflows/service.py's job (via
    app.runtime.service.get_active_agent), kept separate so this
    function stays a pure, DB-free parser."""
    raw_steps = definition_json.get("steps")
    if not isinstance(raw_steps, list) or len(raw_steps) == 0:
        raise WorkflowDefinitionInvalidError(
            workflow_id, "definition_json must have a non-empty 'steps' list"
        )

    parsed: list[tuple[int, WorkflowStepSpec]] = []
    seen_step_ids: set[str] = set()
    seen_orders: set[int] = set()

    # First pass: validate identifiers/order/agent id so step_output
    # mappings in the second pass can check references against a
    # trustworthy set of known step ids.
    raw_by_order: list[tuple[int, dict[str, object]]] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise WorkflowDefinitionInvalidError(workflow_id, "each step must be an object")

        step_id = raw_step.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            raise WorkflowDefinitionInvalidError(
                workflow_id, "each step requires a non-empty step_id"
            )
        if step_id in seen_step_ids:
            raise WorkflowDefinitionInvalidError(workflow_id, f"duplicate step_id {step_id!r}")
        seen_step_ids.add(step_id)

        order = raw_step.get("order")
        if not isinstance(order, int) or isinstance(order, bool):
            raise WorkflowDefinitionInvalidError(
                workflow_id, f"step {step_id!r}: order must be an integer"
            )
        if order in seen_orders:
            raise WorkflowDefinitionInvalidError(workflow_id, f"duplicate order {order}")
        seen_orders.add(order)

        name = raw_step.get("name")
        if not isinstance(name, str) or not name:
            raise WorkflowDefinitionInvalidError(
                workflow_id, f"step {step_id!r}: requires a non-empty name"
            )

        agent_definition_id_raw = raw_step.get("agent_definition_id")
        if not isinstance(agent_definition_id_raw, str):
            raise WorkflowDefinitionInvalidError(
                workflow_id, f"step {step_id!r}: agent_definition_id must be a string"
            )
        try:
            agent_definition_id = uuid.UUID(agent_definition_id_raw)
        except ValueError as exc:
            raise WorkflowDefinitionInvalidError(
                workflow_id, f"step {step_id!r}: agent_definition_id is not a valid UUID"
            ) from exc

        raw_by_order.append((order, raw_step))
        parsed.append(
            (
                order,
                WorkflowStepSpec(
                    step_id=step_id,
                    name=name,
                    agent_definition_id=agent_definition_id,
                    order=order,
                    # placeholder, replaced below once step order is known
                    input_mapping=InputMapping(source="workflow_input"),
                ),
            )
        )

    parsed.sort(key=lambda item: item[0])
    raw_by_order.sort(key=lambda item: item[0])

    resolved: list[WorkflowStepSpec] = []
    known_earlier: set[str] = set()
    for (_order, spec), (_, raw_step) in zip(parsed, raw_by_order, strict=True):
        raw_mapping = raw_step.get("input_mapping")
        if raw_mapping is None:
            mapping = InputMapping(source="workflow_input") if not resolved else InputMapping(
                source="previous_output"
            )
        else:
            mapping = _parse_input_mapping(
                workflow_id,
                spec.step_id,
                raw_mapping,
                known_earlier_step_ids=known_earlier,
                is_first_step=not resolved,
            )
        resolved.append(
            WorkflowStepSpec(
                step_id=spec.step_id,
                name=spec.name,
                agent_definition_id=spec.agent_definition_id,
                order=spec.order,
                input_mapping=mapping,
            )
        )
        known_earlier.add(spec.step_id)

    return resolved

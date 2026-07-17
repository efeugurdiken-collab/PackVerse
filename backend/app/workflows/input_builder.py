"""Single reusable input-building component (Sprint P7, section 7): turns
a step's InputMapping (see app/workflows/definition.py) plus the actual
runtime state of a workflow run into the literal string sent to that
step's agent through the P6 runtime.

Kept as one pure, DB-free function (no SQLAlchemy imports) precisely so
app/workflows/executor.py has exactly one place that builds a step's
input - "Create one reusable input-building component" / "Avoid
duplicating prompt/input construction logic across the executor."

This function trusts that definition.py's parse_workflow_steps has
already statically rejected the invalid shapes it can catch without
runtime state (self-references, forward-references, previous_output on
the first step, unsupported sources). What it additionally guards
against is state that can only be known at execution time - a
step_output/previous_output reference to a step whose output was never
recorded. Sequential, stop-on-failure execution (see
app/workflows/executor.py) means this should never actually happen: by
the time step N runs, every earlier step has already COMPLETED
successfully. The check exists so a bug in the executor's ordering fails
loudly (WorkflowInputResolutionError) instead of silently sending a
step an empty or wrong prompt.
"""
from __future__ import annotations

from app.workflows.definition import InputMapping, WorkflowStepSpec
from app.workflows.exceptions import WorkflowInputResolutionError


def build_step_input(
    step: WorkflowStepSpec,
    *,
    workflow_user_input: str,
    previous_step_id: str | None,
    step_outputs: dict[str, str],
) -> str:
    """Resolves `step`'s input_mapping against the run's actual state.

    workflow_user_input: the original user_input supplied when the
        workflow run was created (POST /workflow-runs) - never mutated.
    previous_step_id: the step_id of the step immediately preceding
        `step` in execution order, or None if `step` is the first step.
        Used only for source == "previous_output".
    step_outputs: every earlier step's persisted output_text, keyed by
        step_id - grows by one entry each time executor.py finishes a
        step. Used for both "previous_output" (looked up via
        previous_step_id) and "step_output" (looked up via the mapping's
        own step_id).
    """
    mapping: InputMapping = step.input_mapping

    if mapping.source == "workflow_input":
        return workflow_user_input

    if mapping.source == "static":
        # parse_workflow_steps guarantees value is a non-None str for
        # this source - see definition.py's _parse_input_mapping.
        assert mapping.value is not None
        return mapping.value

    if mapping.source == "previous_output":
        if previous_step_id is None:
            # Unreachable given definition.py's static rejection of
            # previous_output on the first step, kept as a real check
            # rather than a silent assumption.
            raise WorkflowInputResolutionError(
                step.step_id, "previous_output mapping has no preceding step at execution time"
            )
        if previous_step_id not in step_outputs:
            raise WorkflowInputResolutionError(
                step.step_id,
                f"previous step {previous_step_id!r} has no recorded output yet",
            )
        return step_outputs[previous_step_id]

    # mapping.source == "step_output" - the only remaining case in the
    # closed _MAPPING_SOURCES vocabulary.
    ref = mapping.step_id
    if ref is None or ref not in step_outputs:
        raise WorkflowInputResolutionError(
            step.step_id,
            f"step_output reference {ref!r} has no recorded output yet",
        )
    return step_outputs[ref]

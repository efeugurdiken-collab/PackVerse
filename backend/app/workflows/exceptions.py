"""Workflow domain exceptions (Sprint P7).

Independent hierarchy, same pattern as app.runtime.exceptions and
app.llm.exceptions: app/workflows/service.py and
app/workflows/executor.py raise these, app/api/v1/workflow_runs.py is
the only place they become HTTP status codes - alongside
app.runtime.exceptions.RuntimeDomainError and app.llm.exceptions.LLMError,
both of which can also surface from a step's execution and are mapped
by reusing app/api/v1/runs.py's and app/api/v1/llm.py's existing
mapping functions directly (see that module's docstring) rather than
duplicating a third mapping table.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.enums import WorkflowRunStatus, WorkflowStepRunStatus


class WorkflowDomainError(Exception):
    """Base class for all app.workflows errors."""


class WorkflowNotFoundError(WorkflowDomainError):
    def __init__(self, workflow_id: object) -> None:
        super().__init__(f"Workflow {workflow_id} not found")
        self.workflow_id = workflow_id


class WorkflowNotActiveError(WorkflowDomainError):
    """Raised for a workflow that exists but is DRAFT or DEPRECATED -
    only ACTIVE workflows may be run."""

    def __init__(self, workflow_id: object) -> None:
        super().__init__(f"Workflow {workflow_id} is not active")
        self.workflow_id = workflow_id


class WorkflowDefinitionInvalidError(WorkflowDomainError):
    """Raised for any structural problem with a WorkflowDefinition's
    definition_json: no steps, duplicate step ids/order, a malformed
    step, an invalid input mapping reference, or a step referencing an
    agent that doesn't exist / isn't active. See
    app/workflows/definition.py's parse_workflow_steps."""

    def __init__(self, workflow_id: object, detail: str) -> None:
        super().__init__(f"Workflow {workflow_id} has an invalid definition: {detail}")
        self.workflow_id = workflow_id
        self.detail = detail


class WorkflowRunNotFoundError(WorkflowDomainError):
    """Raised both for a genuinely-missing id and for an id that exists
    but isn't visible to the caller (a non-admin requesting someone
    else's run) - both map to the same 404, so the endpoint can't be
    used to enumerate other users' run ids. See
    app/workflows/service.py's get_run."""

    def __init__(self, run_id: object) -> None:
        super().__init__(f"Workflow run {run_id} not found")
        self.run_id = run_id


class InvalidWorkflowRunTransitionError(WorkflowDomainError):
    def __init__(self, current: "WorkflowRunStatus", target: "WorkflowRunStatus") -> None:
        super().__init__(
            f"Cannot transition workflow run from {current.value!r} to {target.value!r}"
        )
        self.current = current
        self.target = target


class InvalidStepRunTransitionError(WorkflowDomainError):
    def __init__(self, current: "WorkflowStepRunStatus", target: "WorkflowStepRunStatus") -> None:
        super().__init__(
            f"Cannot transition workflow step run from {current.value!r} to {target.value!r}"
        )
        self.current = current
        self.target = target


class WorkflowInputResolutionError(WorkflowDomainError):
    """Raised by app/workflows/input_builder.py if a step's input_mapping
    cannot be resolved against the actual runtime state. Defensive only:
    app/workflows/definition.py's parse_workflow_steps already statically
    rejects self-references, forward-references, and previous_output on
    the first step at definition-parse time, and app/workflows/executor.py
    only builds input for a step after every earlier step has completed
    successfully (sequential, stop-on-failure execution means a
    step_output/previous_output reference to an earlier step can never be
    missing in practice). Kept as a real, raised error rather than an
    assertion so a future change to the execution model fails loudly
    instead of silently producing a wrong prompt."""

    def __init__(self, step_id: str, detail: str) -> None:
        super().__init__(f"Workflow step {step_id!r}: cannot resolve input - {detail}")
        self.step_id = step_id
        self.detail = detail

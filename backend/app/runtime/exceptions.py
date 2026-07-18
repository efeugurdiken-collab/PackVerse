"""Runtime domain exceptions (Sprint P6).

Independent hierarchy, same pattern as app.llm.exceptions.LLMError:
app/runtime/service.py and app/runtime/executor.py raise these,
app/api/v1/runs.py is the only place they become HTTP status codes.
Kept in their own package (not app/services/exceptions.py) because
app.runtime is itself a self-contained package with its own base
exception, mirroring how app.llm keeps LLMError separate from
app.services.exceptions.DomainError.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.enums import AgentRunStatus


class RuntimeDomainError(Exception):
    """Base class for all app.runtime errors."""


class AgentNotFoundError(RuntimeDomainError):
    def __init__(self, agent_id: object) -> None:
        super().__init__(f"Agent {agent_id} not found")
        self.agent_id = agent_id


class AgentNotActiveError(RuntimeDomainError):
    """Raised for an agent that exists but is DRAFT or DEPRECATED - only
    ACTIVE agents may be executed."""

    def __init__(self, agent_id: object) -> None:
        super().__init__(f"Agent {agent_id} is not active")
        self.agent_id = agent_id


class AgentConfigurationError(RuntimeDomainError):
    """Raised when an ACTIVE agent's configuration_json is missing a key
    the prompt builder requires (e.g. system_prompt, model) - a data
    problem with the agent definition, not with the caller's request."""

    def __init__(self, agent_id: object, detail: str) -> None:
        super().__init__(f"Agent {agent_id} is misconfigured: {detail}")
        self.agent_id = agent_id
        self.detail = detail


class AgentRunNotFoundError(RuntimeDomainError):
    """Raised both for a genuinely-missing id and for an id that exists
    but isn't visible to the caller (a non-admin requesting someone
    else's run) - both map to the same 404, so the endpoint can't be
    used to enumerate other users' run ids. See
    app/runtime/service.py's get_run."""

    def __init__(self, run_id: object) -> None:
        super().__init__(f"Agent run {run_id} not found")
        self.run_id = run_id


class ToolLoopLimitExceededError(RuntimeDomainError):
    """Raised by app/runtime/executor.py's _run_tool_loop (Sprint P9C1)
    when the model still wants to call a tool after
    settings.runtime_max_tool_iterations LLM calls - a safety bound
    against a runaway tool-use loop. Always means the configured cap
    was hit, never a signal that tool-calling itself is broken."""

    def __init__(self, agent_id: object, max_iterations: int) -> None:
        super().__init__(
            f"Agent {agent_id} exceeded the tool-call iteration limit "
            f"({max_iterations} LLM calls) without a final answer"
        )
        self.agent_id = agent_id
        self.max_iterations = max_iterations


class InvalidRunTransitionError(RuntimeDomainError):
    """Raised whenever code attempts a status change not present in
    app/runtime/models.py's transition table - e.g. cancelling a run
    that has already completed."""

    def __init__(self, current: "AgentRunStatus", target: "AgentRunStatus") -> None:
        super().__init__(f"Cannot transition run from {current.value!r} to {target.value!r}")
        self.current = current
        self.target = target

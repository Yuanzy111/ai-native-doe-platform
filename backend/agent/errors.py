"""Typed exceptions for the agent module (Agent v0).

These are raised by the agent model client, the output parser, and the agent
application service, and are mapped to stable HTTP error codes in
:mod:`backend.api.errors`. None of them ever carries a raw model response, an
API key, or a provider-internal message beyond the human-readable ``message``.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for every agent-layer error."""


class AgentNotConfiguredError(AgentError):
    """Raised when no agent model is configured (missing ``AGENT_*`` env)."""


class AgentDependencyMissingError(AgentError):
    """Raised when the model is configured but the optional SDK is not installed.

    The ``AGENT_*`` env is present, so the app boots and the model is wired, but
    the optional ``agent`` extra (``openai``) is absent. Surfaced only on first
    :meth:`generate`, never at construction, so the API keeps serving; the agent
    message endpoint then reports ``AGENT_DEPENDENCY_MISSING`` (503). No
    traceback, API key, or vendor payload is ever carried in the message.
    """


class AgentModelError(AgentError):
    """Raised when the underlying model call fails (network/provider error)."""


class InvalidAgentOutputError(AgentError):
    """Raised when the model's output is not a valid :class:`AgentTurn`."""


class AgentInvalidActionError(AgentError):
    """Raised when a model-proposed action carries an invalid domain value.

    The action parsed against the contract but cannot be turned into a valid
    domain object (e.g. a continuous parameter with ``lowerBound >= upperBound``,
    or an empty categorical value set). Distinct from a generic request
    ``VALIDATION_ERROR`` so the client can tell "the model produced a bad action"
    apart from "you sent a bad request"; maps to ``AGENT_INVALID_ACTION``.
    """


class StaleAgentProposalError(AgentError):
    """Raised when a proposal's base revision no longer matches the run.

    The campaign was edited between proposal creation and approval, so applying
    the proposal would silently overwrite the newer revision.
    """


class AgentActionRejectedError(AgentError):
    """Raised when a proposed action is illegal for the run's current state.

    Covers a modification proposed against a lifecycle-frozen design space, an
    op referencing an unknown entity id, and any structurally impossible patch.
    """


__all__ = [
    "AgentError",
    "AgentNotConfiguredError",
    "AgentDependencyMissingError",
    "AgentModelError",
    "InvalidAgentOutputError",
    "AgentInvalidActionError",
    "StaleAgentProposalError",
    "AgentActionRejectedError",
]

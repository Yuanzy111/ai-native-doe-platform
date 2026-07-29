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


class AgentModelError(AgentError):
    """Raised when the underlying model call fails (network/provider error)."""


class InvalidAgentOutputError(AgentError):
    """Raised when the model's output is not a valid :class:`AgentTurn`."""


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
    "AgentModelError",
    "InvalidAgentOutputError",
    "StaleAgentProposalError",
    "AgentActionRejectedError",
]

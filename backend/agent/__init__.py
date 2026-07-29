"""Agent v0: conversational, propose-then-approve design-space editing.

The agent understands experiment requirements in conversation and *proposes*
structured design-space changes; the user approves, and approval dispatches to
the existing deterministic :class:`~backend.application.service.ApplicationService`.
The agent never mutates the campaign directly, never fabricates recommendation
candidates, and never runs shell/code/DB.
"""

from __future__ import annotations

from backend.agent.contract import AgentAction, AgentTurn, PatchOp
from backend.agent.errors import (
    AgentActionRejectedError,
    AgentError,
    AgentModelError,
    AgentNotConfiguredError,
    InvalidAgentOutputError,
    StaleAgentProposalError,
)
from backend.agent.model import (
    AgentModel,
    OpenAICompatibleAgentModel,
    build_agent_model_from_env,
)
from backend.agent.patch import PatchResult, apply_patch

__all__ = [
    "AgentTurn",
    "AgentAction",
    "PatchOp",
    "AgentError",
    "AgentNotConfiguredError",
    "AgentModelError",
    "InvalidAgentOutputError",
    "StaleAgentProposalError",
    "AgentActionRejectedError",
    "AgentModel",
    "OpenAICompatibleAgentModel",
    "build_agent_model_from_env",
    "PatchResult",
    "apply_patch",
]

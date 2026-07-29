"""Agent endpoints (Agent v0, §四).

The four routes are the whole propose-then-approve surface: read the thread,
send a message (which may stage a Pending proposal but never mutates the
campaign), and approve or reject a proposal. Every write is delegated to
:class:`~backend.agent.service.AgentService`; the router assembles nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.agent.service import AgentService
from backend.api.dependencies import get_actor, get_agent_service
from backend.api.schemas import PostAgentMessageRequest

router = APIRouter(prefix="/api/v1/campaign-runs", tags=["agent"])


@router.get("/{run_id}/agent/thread")
def get_agent_thread(
    run_id: str,
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    """Return the run's conversation and any pending proposals (restore view)."""
    return service.get_thread(run_id)


@router.post("/{run_id}/agent/messages")
def post_agent_message(
    run_id: str,
    body: PostAgentMessageRequest,
    actor: str = Depends(get_actor),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    """Record a user message, get one model turn, and stage any proposal.

    Sending a message never mutates the campaign; a proposed action is stored as
    Pending and only applied on a later approval.
    """
    return service.post_message(run_id, actor, body.message)


@router.post("/{run_id}/agent/proposals/{proposal_id}/approve")
def approve_agent_proposal(
    run_id: str,
    proposal_id: str,
    actor: str = Depends(get_actor),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    """Approve a Pending proposal and dispatch it to the existing service."""
    return service.approve_proposal(run_id, proposal_id, actor)


@router.post("/{run_id}/agent/proposals/{proposal_id}/reject")
def reject_agent_proposal(
    run_id: str,
    proposal_id: str,
    actor: str = Depends(get_actor),
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    """Reject a Pending proposal without mutating the campaign."""
    return service.reject_proposal(run_id, proposal_id, actor)


__all__ = ["router"]

"""The agent application service: converse, propose, and (on approval) dispatch.

This is the propose-then-approve boundary (§三/§四). Sending a message NEVER
mutates the campaign, runs validation, or generates a design — it only records
the exchange and, if the model proposed one, stores a **Pending** proposal
pinned to the run's current ``definitionRevisionId``. Only an explicit approval
dispatches to the existing deterministic :class:`ApplicationService`, and only
after the pin still matches (else the proposal is stale).

The agent may never touch the optimizer backend/strategy/policy: a design-space
patch rebuilds the full :class:`DesignSpaceUpdate` from the current policy,
overriding only the acquisition function that the objective count dictates —
mirroring the manual editor's invariant without letting the agent pick a
strategy.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError

from backend.agent.contract import (
    AgentAction,
    AgentTurn,
    DesignSpacePatchAction,
    GenerateInitialDesignAction,
    ValidateDesignSpaceAction,
)
from backend.agent.errors import (
    AgentActionRejectedError,
    AgentNotConfiguredError,
    InvalidAgentOutputError,
    StaleAgentProposalError,
)
from backend.agent.model import AgentModel
from backend.agent.patch import apply_patch
from backend.agent.prompts import SYSTEM_PROMPT, build_context_message
from backend.application import (
    ApplicationService,
    DesignSpaceUpdate,
    EntityNotFoundError,
    ServiceError,
)
from backend.domain.models import (
    AgentMessage,
    AgentMessageRole,
    AgentProposal,
    AgentProposalStatus,
    AgentThread,
    CampaignDefinitionRevision,
    CampaignRun,
    RunStatus,
)
from backend.persistence import SqliteRepository

if TYPE_CHECKING:
    from backend.api.query import RunQueryService

_EDITABLE_STATUSES = {RunStatus.DRAFT, RunStatus.DESIGN_SPACE_VALIDATED}
"""Run states in which the design space may still be modified (§3.6)."""

_ACTION_ADAPTER: TypeAdapter[AgentAction] = TypeAdapter(AgentAction)
"""Reconstructs a stored proposal payload back into a typed action."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class AgentService:
    """Coordinates the conversational, propose-then-approve agent workflow."""

    def __init__(
        self,
        repository: SqliteRepository,
        agent_model: AgentModel | None,
        application_service: ApplicationService,
        query_service: RunQueryService,
    ) -> None:
        """Bind the service to persistence, the model, and the existing services.

        Args:
            repository: The persistence layer for threads/messages/proposals.
            agent_model: The LLM boundary, or ``None`` when unconfigured (message
                sending then raises :class:`AgentNotConfiguredError`).
            application_service: The sole authority for run state changes; every
                approved action dispatches here.
            query_service: The read-side used to return fresh views.
        """
        self._repo = repository
        self._model = agent_model
        self._application = application_service
        self._query = query_service

    # Reads -----------------------------------------------------------------

    def get_thread(self, run_id: str) -> dict[str, Any]:
        """Return a run's conversation and its pending proposals (restore view).

        Raises:
            EntityNotFoundError: If the run does not exist.
        """
        self._query.require_run(run_id)
        thread = self._repo.get_thread_for_run(run_id)
        if thread is None:
            return {"threadId": None, "messages": [], "pendingProposals": []}
        return {
            "threadId": thread.id,
            "messages": [
                _dump(message)
                for message in self._repo.list_agent_messages(thread.id)
            ],
            "pendingProposals": [
                _dump(proposal)
                for proposal in self._repo.list_pending_proposals(run_id)
            ],
        }

    # Conversation ----------------------------------------------------------

    def post_message(self, run_id: str, actor: str, text: str) -> dict[str, Any]:
        """Record a user message, get one model turn, and stage any proposal.

        This never mutates the campaign. If the model proposes a design-space
        patch, its references are validated against the current revision and a
        modification is refused when the run is lifecycle-frozen; a valid action
        becomes a Pending proposal pinned to the current revision.

        Raises:
            AgentNotConfiguredError: If no model is configured.
            EntityNotFoundError: If the run does not exist.
            InvalidAgentOutputError: If the model output is not a valid turn.
            AgentActionRejectedError: If the proposed action is illegal for the
                run's current state (frozen modification / unknown entity id).
        """
        if self._model is None:
            raise AgentNotConfiguredError(
                "No agent model is configured; set the AGENT_* environment."
            )
        message = text.strip()
        if not message:
            raise AgentActionRejectedError("A message must not be empty.")

        run = self._query.require_run(run_id)
        revision = self._require_revision(run)
        thread = self._ensure_thread(run_id)

        self._repo.add_agent_message(
            AgentMessage(
                id=_new_id(),
                thread_id=thread.id,
                role=AgentMessageRole.USER,
                content=message,
                created_at=_now(),
            )
        )

        history = [
            {"role": item.role.value, "content": item.content}
            for item in self._repo.list_agent_messages(thread.id)
        ]
        system = SYSTEM_PROMPT + "\n\n" + build_context_message(run, revision)
        raw = self._model.generate(system, history)
        turn = self._parse_turn(raw)

        self._repo.add_agent_message(
            AgentMessage(
                id=_new_id(),
                thread_id=thread.id,
                role=AgentMessageRole.ASSISTANT,
                content=turn.message,
                created_at=_now(),
            )
        )

        if turn.proposed_action is not None:
            self._stage_proposal(run, revision, thread, turn.proposed_action)

        return self.get_thread(run_id)

    # Approval / rejection --------------------------------------------------

    def approve_proposal(
        self, run_id: str, proposal_id: str, actor: str
    ) -> dict[str, Any]:
        """Approve a Pending proposal and dispatch it to the existing service.

        Raises:
            EntityNotFoundError: If the run or proposal does not exist.
            AgentActionRejectedError: If the proposal is not Pending, or a
                modification is proposed against a frozen run, or the underlying
                action is illegal.
            StaleAgentProposalError: If the campaign changed since the proposal
                was created (its pinned revision no longer matches the run).
        """
        proposal = self._require_proposal(run_id, proposal_id)
        if proposal.status is not AgentProposalStatus.PENDING:
            raise AgentActionRejectedError(
                f"Proposal {proposal_id!r} is {proposal.status.value}, not Pending."
            )
        run = self._query.require_run(run_id)
        if proposal.base_revision_id != run.definition_revision_id:
            raise StaleAgentProposalError(
                "The campaign was edited after this proposal was created; "
                "re-run the agent to propose against the current design space."
            )
        action = _ACTION_ADAPTER.validate_python(dict(proposal.payload))

        initial_design: dict[str, Any] | None = None
        try:
            initial_design = self._dispatch(run, actor, action)
        except (ServiceError, AgentActionRejectedError) as exc:
            self._resolve(proposal, AgentProposalStatus.FAILED, error=str(exc))
            raise

        self._resolve(proposal, AgentProposalStatus.APPROVED)
        return {
            "proposal": _dump(self._repo.get_agent_proposal(proposal_id)),
            "view": self._query.run_view(run_id),
            "initialDesign": initial_design,
        }

    def reject_proposal(
        self, run_id: str, proposal_id: str, actor: str
    ) -> dict[str, Any]:
        """Reject a Pending proposal without mutating the campaign.

        Raises:
            EntityNotFoundError: If the run or proposal does not exist.
            AgentActionRejectedError: If the proposal is not Pending.
        """
        proposal = self._require_proposal(run_id, proposal_id)
        if proposal.status is not AgentProposalStatus.PENDING:
            raise AgentActionRejectedError(
                f"Proposal {proposal_id!r} is {proposal.status.value}, not Pending."
            )
        self._resolve(proposal, AgentProposalStatus.REJECTED)
        return self.get_thread(run_id)

    # Internals -------------------------------------------------------------

    def _dispatch(
        self, run: CampaignRun, actor: str, action: AgentAction
    ) -> dict[str, Any] | None:
        """Apply an approved action via the existing service; return any extra view."""
        if isinstance(action, DesignSpacePatchAction):
            self._reject_if_frozen(run)
            revision = self._require_revision(run)
            update = self._build_update(run, revision, action)
            self._application.save_design_space(run.id, actor, update)
            return None
        if isinstance(action, ValidateDesignSpaceAction):
            self._application.validate_design_space(run.id, actor)
            return None
        if isinstance(action, GenerateInitialDesignAction):
            batch = self._application.generate_initial_design(run.id, actor)
            return self._query.initial_design_view(run.id, batch)
        raise AgentActionRejectedError(  # pragma: no cover - union is exhaustive
            "Unsupported agent action."
        )

    def _build_update(
        self,
        run: CampaignRun,
        revision: CampaignDefinitionRevision,
        action: DesignSpacePatchAction,
    ) -> DesignSpaceUpdate:
        """Rebuild the full design space, preserving the agent-immutable policy."""
        result = apply_patch(revision, action.patch)
        policy = run.optimization_policy
        strategy = policy.strategy_config.model_copy(
            update={"acquisition_function": result.acquisition_function}
        )
        return DesignSpaceUpdate(
            parameters=result.parameters,
            outputs=result.outputs,
            targets=result.targets,
            objective_policy=result.objective_policy,
            constraints=result.constraints,
            constraints_confirmed=result.constraints_confirmed,
            backend_name=policy.backend_name,
            batch_size=policy.batch_size,
            seed_policy=policy.seed_policy,
            seed_value=policy.seed_value,
            strategy_config=strategy,
        )

    def _stage_proposal(
        self,
        run: CampaignRun,
        revision: CampaignDefinitionRevision,
        thread: AgentThread,
        action: AgentAction,
    ) -> None:
        """Validate a proposed action and persist it as Pending (never applied)."""
        if isinstance(action, DesignSpacePatchAction):
            self._reject_if_frozen(run)
            # Dry-run the patch so an unknown-id or structurally impossible op is
            # rejected at proposal time rather than surfacing only on approval.
            apply_patch(revision, action.patch)
        self._repo.add_agent_proposal(
            AgentProposal(
                id=_new_id(),
                thread_id=thread.id,
                campaign_run_id=run.id,
                kind=action.kind,
                payload=action.model_dump(by_alias=True),
                status=AgentProposalStatus.PENDING,
                base_revision_id=run.definition_revision_id,
                created_at=_now(),
            )
        )

    def _resolve(
        self,
        proposal: AgentProposal,
        status: AgentProposalStatus,
        error: str | None = None,
    ) -> None:
        """Persist a proposal's terminal state (Approved/Rejected/Failed)."""
        self._repo.save_agent_proposal(
            proposal.model_copy(
                update={
                    "status": status,
                    "resolved_at": _now(),
                    "error": error,
                }
            )
        )

    def _ensure_thread(self, run_id: str) -> AgentThread:
        """Return the run's thread, creating it on first use (one per run)."""
        return self._repo.get_or_create_thread(
            AgentThread(
                id=_new_id(),
                campaign_run_id=run_id,
                created_at=_now(),
            )
        )

    def _require_revision(
        self, run: CampaignRun
    ) -> CampaignDefinitionRevision:
        """Fetch the run's pinned revision or raise."""
        revision = self._repo.get_revision(run.definition_revision_id)
        if revision is None:
            raise EntityNotFoundError(
                f"Run {run.id!r} pins unknown revision "
                f"{run.definition_revision_id!r}."
            )
        return revision

    def _require_proposal(self, run_id: str, proposal_id: str) -> AgentProposal:
        """Fetch a proposal and assert it belongs to ``run_id``."""
        proposal = self._repo.get_agent_proposal(proposal_id)
        if proposal is None or proposal.campaign_run_id != run_id:
            raise EntityNotFoundError(
                f"Unknown proposal {proposal_id!r} for run {run_id!r}."
            )
        return proposal

    @staticmethod
    def _reject_if_frozen(run: CampaignRun) -> None:
        """Refuse a design-space modification once the run's lifecycle has advanced."""
        if run.status not in _EDITABLE_STATUSES:
            raise AgentActionRejectedError(
                f"The design space is frozen in state {run.status.value!r}; the "
                "agent cannot modify it after an initial design exists."
            )

    @staticmethod
    def _parse_turn(raw: str) -> AgentTurn:
        """Parse the model's raw text into an :class:`AgentTurn`."""
        try:
            return AgentTurn.model_validate_json(raw)
        except ValidationError as exc:
            raise InvalidAgentOutputError(
                "The agent produced output that is not a valid turn."
            ) from exc
        except ValueError as exc:
            raise InvalidAgentOutputError(
                "The agent produced output that is not valid JSON."
            ) from exc


def _dump(model: Any) -> Any:
    """Serialize a domain model to camelCase JSON, or pass ``None`` through."""
    if model is None:
        return None
    return model.model_dump(mode="json", by_alias=True)


__all__ = ["AgentService"]

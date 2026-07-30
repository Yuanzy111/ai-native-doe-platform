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
    AgentInvalidActionError,
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
from backend.domain.validation import StateTransitionError
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

_MAX_HISTORY_MESSAGES = 20
"""How many recent turns are sent to the model; the full thread stays in SQLite."""

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

        The turn is **atomic** (§6): nothing is written before the model call,
        and the model is called outside any transaction. After the reply is
        parsed the proposed action is state-gated and dry-run, and the run is
        re-read to confirm its revision did not change during the (slow) model
        call. Only then, in a *single* transaction, are the thread, the user
        message, the assistant message, and any Pending proposal persisted — so a
        model timeout, invalid JSON, invalid action, or a concurrent edit leaves
        no orphaned message or proposal behind.

        Raises:
            AgentNotConfiguredError: If no model is configured.
            AgentDependencyMissingError: If the optional SDK is not installed.
            EntityNotFoundError: If the run does not exist.
            InvalidAgentOutputError: If the model output is not a valid turn.
            AgentInvalidActionError: If the action carries an invalid domain value.
            AgentActionRejectedError: If the proposed action is illegal for the
                run's current state (wrong lifecycle state / unknown entity id).
            StaleAgentProposalError: If the run was edited during the model call.
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

        # Build the model input from persisted history plus the new user message,
        # held only in memory; nothing is written yet.
        existing_thread = self._repo.get_thread_for_run(run_id)
        prior = (
            self._repo.list_agent_messages(existing_thread.id)
            if existing_thread is not None
            else []
        )
        history = [{"role": item.role.value, "content": item.content} for item in prior]
        history.append({"role": "user", "content": message})
        history = history[-_MAX_HISTORY_MESSAGES:]

        batches = self._repo.list_batches(run_id)
        latest_batch = batches[-1] if batches else None
        experiment_runs = (
            self._repo.list_experiment_runs_for_run(run_id) if latest_batch else []
        )
        system = (
            SYSTEM_PROMPT
            + "\n\n"
            + build_context_message(run, revision, latest_batch, experiment_runs)
        )

        # The slow, side-effect-free model call happens with no write lock held.
        raw = self._model.generate(system, history)
        turn = self._parse_turn(raw)

        # Validate any proposed action before writing anything (§4/§6).
        if turn.proposed_action is not None:
            self._validate_action(run, revision, turn.proposed_action)

        # Confirm the design space did not move under us during the model call.
        fresh_run = self._query.require_run(run_id)
        if fresh_run.definition_revision_id != run.definition_revision_id:
            raise StaleAgentProposalError(
                "The campaign was edited while the agent was responding; "
                "re-send the message to work against the current design space."
            )

        with self._repo.transaction():
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
                self._repo.add_agent_proposal(
                    AgentProposal(
                        id=_new_id(),
                        thread_id=thread.id,
                        campaign_run_id=run.id,
                        kind=turn.proposed_action.kind,
                        payload=turn.proposed_action.model_dump(by_alias=True),
                        status=AgentProposalStatus.PENDING,
                        base_revision_id=run.definition_revision_id,
                        created_at=_now(),
                    )
                )

        return self.get_thread(run_id)

    # Approval / rejection --------------------------------------------------

    def approve_proposal(
        self, run_id: str, proposal_id: str, actor: str
    ) -> dict[str, Any]:
        """Approve a Pending proposal and dispatch it to the existing service.

        The dispatch and the proposal's move to Approved happen in **one** outer
        transaction (§7): the ``ApplicationService``'s own transaction joins it,
        so the campaign write and the proposal update commit or roll back
        together. If dispatch fails the outer transaction rolls the campaign
        change back, and only then is the proposal marked Failed in a *separate*
        transaction — the campaign is never left mutated while the proposal
        stays Pending. A stale or illegal proposal is rejected before any write,
        leaving it Pending so it can still be rejected by the user.

        Raises:
            EntityNotFoundError: If the run or proposal does not exist.
            AgentActionRejectedError: If the proposal is not Pending, or the
                action is illegal for the run's current state.
            AgentInvalidActionError: If the action carries an invalid domain value.
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
        # Re-check state gates against the live run; on failure the proposal is
        # left Pending (nothing written) so the user can still reject it.
        self._reject_illegal_action(run, action)

        try:
            with self._repo.transaction():
                initial_design, validation_result = self._dispatch(run, actor, action)
                self._resolve(proposal, AgentProposalStatus.APPROVED)
        except (
            ServiceError,
            StateTransitionError,
            AgentActionRejectedError,
            AgentInvalidActionError,
        ) as exc:
            # The outer transaction rolled the campaign change back; record the
            # failure on the proposal in its own transaction.
            self._resolve(proposal, AgentProposalStatus.FAILED, error=str(exc))
            raise

        return {
            "proposal": _dump(self._repo.get_agent_proposal(proposal_id)),
            "view": self._query.run_view(run_id),
            "initialDesign": initial_design,
            "validationResult": validation_result,
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
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Apply an approved action via the existing service.

        Returns a ``(initialDesign, validationResult)`` pair: at most one side is
        populated (a patch populates neither, generate the first, validate the
        second), so the caller can surface a real validation outcome (§5) instead
        of equating "approved" with "validation passed".
        """
        if isinstance(action, DesignSpacePatchAction):
            revision = self._require_revision(run)
            update = self._build_update(run, revision, action)
            self._application.save_design_space(run.id, actor, update)
            return None, None
        if isinstance(action, ValidateDesignSpaceAction):
            result = self._application.validate_design_space(run.id, actor)
            return None, self._validation_view(result)
        if isinstance(action, GenerateInitialDesignAction):
            batch = self._application.generate_initial_design(run.id, actor)
            return self._query.initial_design_view(run.id, batch), None
        raise AgentActionRejectedError(  # pragma: no cover - union is exhaustive
            "Unsupported agent action."
        )

    @staticmethod
    def _validation_view(result: Any) -> dict[str, Any]:
        """Shape a :class:`ValidationResult` like the ``/validate`` endpoint."""
        return {"ok": result.ok, **result.model_dump(mode="json", by_alias=True)}

    def _build_update(
        self,
        run: CampaignRun,
        revision: CampaignDefinitionRevision,
        action: DesignSpacePatchAction,
    ) -> DesignSpaceUpdate:
        """Rebuild the full design space, preserving the agent-immutable policy."""
        result = self._apply_patch(revision, action.patch)
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

    def _validate_action(
        self,
        run: CampaignRun,
        revision: CampaignDefinitionRevision,
        action: AgentAction,
    ) -> None:
        """Reject an action illegal for the run's state, and dry-run a patch.

        Never writes anything. State gating (§4) is applied to every action; a
        design-space patch is additionally dry-run so an unknown-id or
        invalid-value op is rejected now rather than surfacing only on approval.
        """
        self._reject_illegal_action(run, action)
        if isinstance(action, DesignSpacePatchAction):
            self._apply_patch(revision, action.patch)

    def _reject_illegal_action(self, run: CampaignRun, action: AgentAction) -> None:
        """Deterministically forbid an action illegal for the run's state (§4).

        Only ``Draft``/``DesignSpaceValidated`` runs with no batch may be patched;
        validation is offered only from ``Draft``; an initial design may be
        generated only from ``DesignSpaceValidated`` with no batch. Every later
        state (``RecommendationsPending`` onward) allows conversation only.
        """
        if isinstance(action, DesignSpacePatchAction):
            if run.status not in _EDITABLE_STATUSES:
                raise AgentActionRejectedError(
                    f"The design space is frozen in state {run.status.value!r}; "
                    "the agent cannot modify it after an initial design exists."
                )
            if self._repo.list_batches(run.id):
                raise AgentActionRejectedError(
                    "The design space is frozen once a recommendation batch "
                    "exists; the agent cannot modify it."
                )
        elif isinstance(action, ValidateDesignSpaceAction):
            if run.status is not RunStatus.DRAFT:
                raise AgentActionRejectedError(
                    f"Validation is only available for a Draft run, not "
                    f"{run.status.value!r}."
                )
        elif isinstance(action, GenerateInitialDesignAction):
            if run.status is not RunStatus.DESIGN_SPACE_VALIDATED:
                raise AgentActionRejectedError(
                    "An initial design can only be generated from a validated "
                    f"design space, not from {run.status.value!r}."
                )
            if self._repo.list_batches(run.id):
                raise AgentActionRejectedError(
                    "An initial design has already been generated for this run."
                )

    @staticmethod
    def _apply_patch(revision: CampaignDefinitionRevision, op: Any) -> Any:
        """Apply a patch, mapping a domain ``ValidationError`` to a stable code.

        ``apply_patch`` builds real domain objects, so a model-proposed value the
        contract could not reject (e.g. ``lowerBound >= upperBound`` or an empty
        categorical set) raises a pydantic ``ValidationError`` here; it is
        surfaced as :class:`AgentInvalidActionError` (AGENT_INVALID_ACTION) rather
        than a generic request validation error.
        """
        try:
            return apply_patch(revision, op)
        except ValidationError as exc:
            raise AgentInvalidActionError(
                "The proposed change contains an invalid value and cannot be "
                "applied."
            ) from exc

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

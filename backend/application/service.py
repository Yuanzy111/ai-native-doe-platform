"""The application service: the sole authority over run state changes.

Every lifecycle mutation of a :class:`CampaignRun` funnels through this service so
the state machine (§3.2), the post-first-batch freeze (§3.6), the derived
counters (``round``/``budgetUsed``), and cross-aggregate ownership stay
consistent.

There is deliberately no generic ``transition(event)`` escape hatch: a caller
cannot self-declare that a definition passed validation, that a round closed, or
that a run completed. Instead each intent is a named method that performs the
real work behind the corresponding event — ``validate_design_space`` actually
runs :func:`validate_definition`, ``close_round`` actually closes the
:class:`ExperimentRound`, ``abort_round`` actually cancels its open experiments,
and so on — and only then applies the state transition.

The optimizer adapter is intentionally absent, so the two events that depend on
it (initial-design generation and recommendation) are not offered as callable
operations yet: they must eventually run in the same transaction as the batch,
round, and transition they produce, which cannot be assembled without the
adapter. :meth:`ApplicationService.generate_initial_design` and
:meth:`ApplicationService.recommend` raise :class:`NotImplementedError` to make
that boundary explicit rather than allow a half-applied lifecycle jump.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from backend.domain.models import (
    BatchStatus,
    CampaignDefinition,
    CampaignDefinitionRevision,
    CampaignRun,
    DecisionAction,
    DecisionLog,
    ExperimentRound,
    ExperimentRunStatus,
    OptimizationPolicy,
    RoundStatus,
    RunStatus,
)
from backend.domain.validation import (
    RunEvent,
    ValidationResult,
    next_status,
    validate_definition,
)
from backend.persistence import PersistenceError, SqliteRepository

_BUDGET_CONSUMING = {ExperimentRunStatus.COMPLETED, ExperimentRunStatus.FAILED}
"""The experiment-run statuses that consume one unit of budget."""


class ServiceError(Exception):
    """Raised when an application-level invariant is violated."""


def _now() -> datetime:
    """Return the current timezone-aware timestamp."""
    return datetime.now(timezone.utc)


class ApplicationService:
    """Coordinates run state changes over a :class:`SqliteRepository`."""

    def __init__(self, repository: SqliteRepository) -> None:
        """Bind the service to a repository.

        Args:
            repository: The persistence layer used for all reads and writes.
        """
        self._repo = repository

    # Campaign creation -----------------------------------------------------

    def create_campaign(
        self,
        definition: CampaignDefinition,
        first_revision: CampaignDefinitionRevision,
    ) -> None:
        """Create a definition and its first revision atomically (§2.1-§2.2).

        Args:
            definition: The container; its ``headRevisionId`` must point at
                ``first_revision``.
            first_revision: The revision-1 snapshot owned by ``definition``.

        Raises:
            ServiceError: If the two do not reference each other consistently.
            PersistenceError: If either insert violates a persistence invariant
                (the whole unit of work rolls back).
        """
        if first_revision.campaign_definition_id != definition.id:
            raise ServiceError(
                "The first revision must belong to the definition being created."
            )
        if first_revision.revision_number != 1:
            raise ServiceError("The first revision must have revisionNumber=1.")
        if definition.head_revision_id != first_revision.id:
            raise ServiceError(
                "definition.headRevisionId must point at the first revision."
            )
        with self._repo.transaction():
            self._repo.add_definition(definition)
            self._repo.add_revision(first_revision)

    def add_revision(
        self, revision: CampaignDefinitionRevision
    ) -> CampaignDefinition:
        """Append a revision and advance the definition head atomically (§2.2).

        Args:
            revision: The revision to append; must extend the existing chain.

        Returns:
            The updated definition with ``headRevisionId`` set to ``revision.id``.

        Raises:
            ServiceError: If the owning definition does not exist.
            PersistenceError: If the revision does not extend the chain.
        """
        with self._repo.transaction():
            definition = self._repo.get_definition(revision.campaign_definition_id)
            if definition is None:
                raise ServiceError(
                    f"Unknown definition {revision.campaign_definition_id!r}."
                )
            self._repo.add_revision(revision)
            updated = definition.model_copy(
                update={"head_revision_id": revision.id, "updated_at": _now()}
            )
            self._repo.save_definition(updated)
            return updated

    # Run creation ----------------------------------------------------------

    def create_run(self, run: CampaignRun) -> CampaignRun:
        """Create a campaign run in a clean initial state (§3.1).

        A freshly created run must start as a ``Draft`` with no rounds closed and
        no budget consumed, and its pinned revision must exist and belong to the
        run's campaign definition.

        Args:
            run: The run to create.

        Returns:
            The persisted run.

        Raises:
            ServiceError: If the initial status/counters are not clean, or the
                pinned revision is unknown or belongs to another campaign.
            PersistenceError: If the insert violates a persistence invariant.
        """
        if run.status is not RunStatus.DRAFT:
            raise ServiceError("A new run must start in the Draft state.")
        if run.round != 0:
            raise ServiceError("A new run must start with round=0.")
        if run.budget_used != 0:
            raise ServiceError("A new run must start with budgetUsed=0.")
        with self._repo.transaction():
            revision = self._repo.get_revision(run.definition_revision_id)
            if revision is None:
                raise ServiceError(
                    f"Unknown revision {run.definition_revision_id!r}."
                )
            if revision.campaign_definition_id != run.campaign_definition_id:
                raise ServiceError(
                    "The pinned revision must belong to the run's campaign "
                    "definition."
                )
            self._repo.add_run(run)
            return run

    # Run state changes -----------------------------------------------------

    def validate_design_space(self, run_id: str, actor: str) -> ValidationResult:
        """Validate the run's pinned revision and record the outcome (§4).

        The pass/fail decision is made by :func:`validate_definition`, never by
        the caller. A clean result advances a ``Draft`` run to
        ``DesignSpaceValidated``; a blocking result records the failure and
        leaves the run in ``Draft``.

        Args:
            run_id: The run to validate.
            actor: The identity recorded in the decision log.

        Returns:
            The :class:`ValidationResult` for inspection.

        Raises:
            ServiceError: If the run or its pinned revision does not exist.
            StateTransitionError: If the run is not in a state that permits
                validation.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            revision = self._repo.get_revision(run.definition_revision_id)
            if revision is None:
                raise ServiceError(
                    f"Run {run_id!r} pins unknown revision "
                    f"{run.definition_revision_id!r}."
                )
            result = validate_definition(revision)
            if result.ok:
                self._transition(
                    run,
                    RunEvent.VALIDATE_DEFINITION_PASS,
                    actor,
                    DecisionAction.DESIGN_SPACE_VALIDATED,
                )
            else:
                self._transition(
                    run,
                    RunEvent.VALIDATE_DEFINITION_FAIL,
                    actor,
                    DecisionAction.DESIGN_SPACE_VALIDATION_FAILED,
                )
            return result

    def edit_definition(self, run_id: str, actor: str) -> CampaignRun:
        """Return a validated run to ``Draft`` for further editing (§3.2).

        Args:
            run_id: The run to reopen for editing.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in the ``Draft`` state.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            return self._transition(run, RunEvent.EDIT_DEFINITION, actor, None)

    def generate_initial_design(self, run_id: str, actor: str) -> None:
        """Not available until the optimizer adapter exists (§4.1).

        Initial-design generation must persist a batch, open a round, and
        transition the run in one transaction. That cannot be assembled without
        the adapter, so this operation is intentionally unavailable rather than
        letting a caller jump the run's state on its own.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "generate_initial_design requires the optimizer adapter; it must "
            "create the batch, round, and transition atomically and cannot be "
            "triggered on its own yet."
        )

    def recommend(self, run_id: str, actor: str) -> None:
        """Not available until the optimizer adapter exists (§4.1).

        Recommendation must persist a batch, open a round, and transition the run
        in one transaction. That cannot be assembled without the adapter, so this
        operation is intentionally unavailable rather than letting a caller jump
        the run's state on its own.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "recommend requires the optimizer adapter; it must create the "
            "batch, round, and transition atomically and cannot be triggered on "
            "its own yet."
        )

    def mark_all_runs_terminal(self, run_id: str, actor: str) -> CampaignRun:
        """Move a pending round to awaiting-measurements once execution ends (§3.2).

        The run may only leave ``RecommendationsPending`` when its open round has
        no experiment still ``Pending``; otherwise the physical work is not
        actually finished.

        Args:
            run_id: The run to advance.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in the ``AwaitingMeasurements`` state.

        Raises:
            ServiceError: If there is no open round, or an experiment is still
                pending.
            StateTransitionError: If the transition is not permitted.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            open_round = self._current_open_round(run_id)
            if open_round is None:
                raise ServiceError(
                    f"Run {run_id!r} has no open round to conclude."
                )
            pending = [
                experiment
                for experiment in self._repo.list_experiment_runs(open_round.id)
                if experiment.status is ExperimentRunStatus.PENDING
            ]
            if pending:
                raise ServiceError(
                    "Cannot mark all runs terminal while experiments are still "
                    f"pending: {[experiment.id for experiment in pending]}."
                )
            return self._transition(run, RunEvent.ALL_RUNS_TERMINAL, actor, None)

    def close_round(self, run_id: str, round_id: str, actor: str) -> CampaignRun:
        """Close a run's open round and advance the run in one transaction (§3.2).

        Args:
            run_id: The run that owns the round.
            round_id: The round to close.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in the ``RoundClosed`` state.

        Raises:
            ServiceError: If the round does not exist, belongs to another run, or
                is not open.
            StateTransitionError: If the run cannot close a round now.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            experiment_round = self._require_round(round_id, run_id)
            if experiment_round.status is not RoundStatus.OPEN:
                raise ServiceError(f"Round {round_id!r} is already closed.")
            updated = self._transition(
                run, RunEvent.CLOSE_ROUND, actor, DecisionAction.ROUND_CLOSED
            )
            self._repo.save_round(
                experiment_round.model_copy(
                    update={"status": RoundStatus.CLOSED, "closed_at": _now()}
                )
            )
            return updated

    def abort_round(self, run_id: str, round_id: str, actor: str) -> CampaignRun:
        """Abort a run's open round, cancelling and superseding its work (§3.2).

        Aborting cancels every still-pending experiment in the round, closes the
        round, and supersedes the originating recommendation batch, then
        transitions the run — all in one transaction.

        Args:
            run_id: The run that owns the round.
            round_id: The round to abort.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in the ``RoundClosed`` state.

        Raises:
            ServiceError: If the round does not exist, belongs to another run, or
                is not open.
            StateTransitionError: If the run cannot abort a round now.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            experiment_round = self._require_round(round_id, run_id)
            if experiment_round.status is not RoundStatus.OPEN:
                raise ServiceError(f"Round {round_id!r} is already closed.")
            updated = self._transition(
                run, RunEvent.ABORT_ROUND, actor, DecisionAction.ROUND_ABORTED
            )
            for experiment in self._repo.list_experiment_runs(round_id):
                if experiment.status is ExperimentRunStatus.PENDING:
                    self._repo.save_experiment_run(
                        experiment.model_copy(
                            update={"status": ExperimentRunStatus.CANCELLED}
                        )
                    )
            self._repo.save_round(
                experiment_round.model_copy(
                    update={"status": RoundStatus.CLOSED, "closed_at": _now()}
                )
            )
            batch = self._repo.get_batch(experiment_round.recommendation_batch_id)
            if batch is not None and batch.status is not BatchStatus.SUPERSEDED:
                self._repo.save_batch(
                    batch.model_copy(update={"status": BatchStatus.SUPERSEDED})
                )
            return updated

    def mark_completed(self, run_id: str, actor: str) -> CampaignRun:
        """Complete a run once no round remains open (§3.2).

        Args:
            run_id: The run to complete.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in the ``Completed`` state.

        Raises:
            ServiceError: If any round is still open.
            StateTransitionError: If the run cannot complete now.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            open_rounds = [
                experiment_round
                for experiment_round in self._repo.list_rounds(run_id)
                if experiment_round.status is RoundStatus.OPEN
            ]
            if open_rounds:
                raise ServiceError(
                    "Cannot complete a run with open rounds: "
                    f"{[experiment_round.id for experiment_round in open_rounds]}."
                )
            return self._transition(
                run, RunEvent.MARK_COMPLETED, actor, DecisionAction.RUN_COMPLETED
            )

    def reopen(self, run_id: str, actor: str) -> CampaignRun:
        """Reopen a completed run for another round (§3.2).

        Args:
            run_id: The run to reopen.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in the ``RoundClosed`` state.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            return self._transition(
                run, RunEvent.REOPEN, actor, DecisionAction.RUN_REOPENED
            )

    def archive(self, run_id: str, actor: str) -> CampaignRun:
        """Archive a run from a terminal-eligible state (§3.2).

        Args:
            run_id: The run to archive.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in the ``Archived`` state.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            return self._transition(
                run, RunEvent.ARCHIVE, actor, DecisionAction.RUN_ARCHIVED
            )

    def update_policy(
        self, run_id: str, policy: OptimizationPolicy
    ) -> CampaignRun:
        """Replace a run's optimization policy before its first batch (§3.6).

        Args:
            run_id: The run to update.
            policy: The replacement policy.

        Returns:
            The saved run.

        Raises:
            ServiceError: If the run does not exist, or a batch already exists.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            self._reject_if_batched(run_id, "optimizationPolicy")
            updated = run.model_copy(
                update={"optimization_policy": policy, "updated_at": _now()}
            )
            self._repo.save_run(updated)
            return updated

    def repin_revision(self, run_id: str, revision_id: str) -> CampaignRun:
        """Repin a run's definition revision before its first batch (§3.6).

        The target revision must exist and belong to the run's campaign, and the
        run must not yet have produced a batch.

        Args:
            run_id: The run to update.
            revision_id: The revision id to pin.

        Returns:
            The saved run.

        Raises:
            ServiceError: If the run does not exist, a batch already exists, or
                the target revision is unknown or belongs to another campaign.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            self._reject_if_batched(run_id, "definitionRevisionId")
            revision = self._repo.get_revision(revision_id)
            if revision is None:
                raise ServiceError(f"Unknown revision {revision_id!r}.")
            if revision.campaign_definition_id != run.campaign_definition_id:
                raise ServiceError(
                    "The target revision must belong to the run's campaign "
                    "definition."
                )
            updated = run.model_copy(
                update={"definition_revision_id": revision_id, "updated_at": _now()}
            )
            self._repo.save_run(updated)
            return updated

    def recompute_counters(self, run_id: str) -> CampaignRun:
        """Derive ``round`` and ``budgetUsed`` from persisted entities (§3.5).

        ``round`` is the number of closed experiment rounds; ``budgetUsed`` is the
        number of experiment runs in a budget-consuming (Completed/Failed) status.

        Args:
            run_id: The run whose counters to recompute.

        Returns:
            The saved run with reconciled counters.

        Raises:
            ServiceError: If the run does not exist.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            rounds = self._repo.list_rounds(run_id)
            closed = sum(1 for r in rounds if r.status is RoundStatus.CLOSED)
            experiments = self._repo.list_experiment_runs_for_run(run_id)
            consumed = sum(
                1 for e in experiments if e.status in _BUDGET_CONSUMING
            )
            updated = run.model_copy(
                update={
                    "round": closed,
                    "budget_used": consumed,
                    "updated_at": _now(),
                }
            )
            self._repo.save_run(updated)
            return updated

    # Internals -------------------------------------------------------------

    def _transition(
        self,
        run: CampaignRun,
        event: RunEvent,
        actor: str,
        action: DecisionAction | None,
        extra_update: dict[str, Any] | None = None,
    ) -> CampaignRun:
        """Apply ``event`` to ``run``, save it, and append an audit entry.

        The next status comes from the §3.2 state machine, so an illegal event
        raises :class:`StateTransitionError` before anything is written.
        """
        update: dict[str, Any] = {
            "status": next_status(run.status, event),
            "updated_at": _now(),
        }
        if extra_update:
            update.update(extra_update)
        updated = run.model_copy(update=update)
        self._repo.save_run(updated)
        if action is not None:
            self._repo.append_decision_log(
                DecisionLog(
                    id=str(uuid.uuid4()),
                    campaign_run_id=run.id,
                    timestamp=_now(),
                    actor=actor,
                    action=action,
                    definition_revision_id=run.definition_revision_id,
                )
            )
        return updated

    def _require_run(self, run_id: str) -> CampaignRun:
        """Fetch a run or raise a :class:`ServiceError`."""
        run = self._repo.get_run(run_id)
        if run is None:
            raise ServiceError(f"Unknown run {run_id!r}.")
        return run

    def _require_round(self, round_id: str, run_id: str) -> ExperimentRound:
        """Fetch a round and assert it belongs to ``run_id``."""
        experiment_round = self._repo.get_round(round_id)
        if experiment_round is None:
            raise ServiceError(f"Unknown round {round_id!r}.")
        if experiment_round.campaign_run_id != run_id:
            raise ServiceError(
                f"Round {round_id!r} does not belong to run {run_id!r}."
            )
        return experiment_round

    def _current_open_round(self, run_id: str) -> ExperimentRound | None:
        """Return the open round with the highest round number, if any."""
        open_rounds = [
            experiment_round
            for experiment_round in self._repo.list_rounds(run_id)
            if experiment_round.status is RoundStatus.OPEN
        ]
        if not open_rounds:
            return None
        return max(open_rounds, key=lambda experiment_round: experiment_round.round_number)

    def _reject_if_batched(self, run_id: str, field: str) -> None:
        """Forbid editing a frozen field once the run has a batch (§3.6)."""
        if self._repo.list_batches(run_id):
            raise ServiceError(
                f"{field} is frozen after the first recommendation batch."
            )


__all__ = ["ApplicationService", "ServiceError", "PersistenceError"]

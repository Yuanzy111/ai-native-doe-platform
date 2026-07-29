"""The minimal application service: the sole authority over run state changes.

Every lifecycle mutation of a :class:`CampaignRun` funnels through this service so
the state-machine (§3.2), the post-first-batch freeze (§3.6), and the derived
counters (``round``/``budgetUsed``) stay consistent. Callers never write an
arbitrary ``status`` directly; they name a :class:`RunEvent` and the service
computes the next status, saves the run, and appends a :class:`DecisionLog` inside
one transaction.

The optimizer adapter is intentionally absent: this service orchestrates
persistence and invariants only, not candidate generation.
"""

import uuid
from datetime import datetime, timezone

from backend.domain.models import (
    CampaignDefinition,
    CampaignDefinitionRevision,
    CampaignRun,
    DecisionAction,
    DecisionLog,
    OptimizationPolicy,
    RoundStatus,
    ExperimentRunStatus,
)
from backend.domain.validation import RunEvent, next_status
from backend.persistence import PersistenceError, SqliteRepository

_EVENT_ACTIONS: dict[RunEvent, DecisionAction] = {
    RunEvent.VALIDATE_DEFINITION_PASS: DecisionAction.DESIGN_SPACE_VALIDATED,
    RunEvent.VALIDATE_DEFINITION_FAIL: DecisionAction.DESIGN_SPACE_VALIDATION_FAILED,
    RunEvent.GENERATE_INITIAL_DESIGN: DecisionAction.INITIAL_DESIGN_GENERATED,
    RunEvent.RECOMMEND: DecisionAction.RECOMMENDATION_REQUESTED,
    RunEvent.CLOSE_ROUND: DecisionAction.ROUND_CLOSED,
    RunEvent.ABORT_ROUND: DecisionAction.ROUND_ABORTED,
    RunEvent.MARK_COMPLETED: DecisionAction.RUN_COMPLETED,
    RunEvent.ARCHIVE: DecisionAction.RUN_ARCHIVED,
    RunEvent.REOPEN: DecisionAction.RUN_REOPENED,
}
"""The :class:`RunEvent` -> :class:`DecisionAction` map for audit logging.

Events with no clean audit action (``EDIT_DEFINITION``, ``ALL_RUNS_TERMINAL``)
transition the run without appending a log entry.
"""

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

    # Run state changes -----------------------------------------------------

    def transition(
        self, run_id: str, event: RunEvent, actor: str
    ) -> CampaignRun:
        """Apply a lifecycle ``event`` to a run — the only way to change status.

        Args:
            run_id: The run to transition.
            event: The lifecycle event; the resulting status is computed by the
                §3.2 state machine.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in its new status.

        Raises:
            ServiceError: If the run does not exist.
            StateTransitionError: If the event is illegal in the current status.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            new_status = next_status(run.status, event)
            updated = run.model_copy(
                update={"status": new_status, "updated_at": _now()}
            )
            self._repo.save_run(updated)
            action = _EVENT_ACTIONS.get(event)
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

        Args:
            run_id: The run to update.
            revision_id: The revision id to pin.

        Returns:
            The saved run.

        Raises:
            ServiceError: If the run does not exist, or a batch already exists.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            self._reject_if_batched(run_id, "definitionRevisionId")
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

    def _require_run(self, run_id: str) -> CampaignRun:
        """Fetch a run or raise a :class:`ServiceError`."""
        run = self._repo.get_run(run_id)
        if run is None:
            raise ServiceError(f"Unknown run {run_id!r}.")
        return run

    def _reject_if_batched(self, run_id: str, field: str) -> None:
        """Forbid editing a frozen field once the run has a batch (§3.6)."""
        if self._repo.list_batches(run_id):
            raise ServiceError(
                f"{field} is frozen after the first recommendation batch."
            )


__all__ = ["ApplicationService", "ServiceError", "PersistenceError"]

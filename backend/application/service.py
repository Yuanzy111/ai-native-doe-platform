"""The application service: the sole authority over run state changes.

Every lifecycle mutation of a :class:`CampaignRun` funnels through this service so
the state machine (§3.2), the post-first-batch freeze (§3.6), the derived
counters (``round``/``budgetUsed``), and cross-aggregate ownership stay
consistent.

There is deliberately no generic ``transition(event)`` escape hatch: a caller
cannot self-declare that a definition passed validation, that a round is ready to
close, or that a run completed. Instead each intent is a named method that
performs the real work behind the corresponding event — ``validate_design_space``
actually runs :func:`validate_definition`, ``generate_initial_design`` actually
calls the optimizer adapter and persists the batch/round/experiments,
``close_round`` actually calls :func:`assess_readiness`, ``abort_round`` actually
cancels its open experiments — and only then applies the state transition.

The optimizer boundary is an injected :class:`OptimizerAdapter`. When no adapter
is configured the adapter-dependent operations raise :class:`NotImplementedError`
rather than letting a caller jump the run's state on its own.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from backend.application.adapter import OptimizerAdapter
from backend.domain.models import (
    BatchStatus,
    CampaignDefinition,
    CampaignDefinitionRevision,
    CampaignRun,
    DecisionAction,
    DecisionLog,
    ExperimentRound,
    ExperimentRun,
    ExperimentRunStatus,
    Measurement,
    OptimizationPolicy,
    RecommendationBatch,
    RoundStatus,
    RunStatus,
)
from backend.domain.validation import (
    RunEvent,
    ValidationResult,
    assess_readiness,
    next_status,
    validate_candidates,
    validate_definition,
)
from backend.persistence import PersistenceError, SqliteRepository

_BUDGET_CONSUMING = {ExperimentRunStatus.COMPLETED, ExperimentRunStatus.FAILED}
"""The experiment-run statuses that consume one unit of budget."""

_TERMINAL_EXPERIMENT = {
    ExperimentRunStatus.COMPLETED,
    ExperimentRunStatus.FAILED,
    ExperimentRunStatus.CANCELLED,
}
"""The experiment-run statuses that count as physically finished."""

_EDITABLE_STATUSES = {RunStatus.DRAFT, RunStatus.DESIGN_SPACE_VALIDATED}
"""The run states in which the policy/pinned revision may still be edited (§3.6)."""

_MEASURABLE_STATUSES = {
    RunStatus.RECOMMENDATIONS_PENDING,
    RunStatus.AWAITING_MEASUREMENTS,
}
"""The run states in which measurements may still be recorded (§3.5)."""


class ServiceError(Exception):
    """Raised when an application-level invariant is violated."""


def _now() -> datetime:
    """Return the current timezone-aware timestamp."""
    return datetime.now(timezone.utc)


class ApplicationService:
    """Coordinates run state changes over a :class:`SqliteRepository`."""

    def __init__(
        self,
        repository: SqliteRepository,
        adapter: OptimizerAdapter | None = None,
    ) -> None:
        """Bind the service to a repository and an optional optimizer adapter.

        Args:
            repository: The persistence layer used for all reads and writes.
            adapter: The optimizer boundary used to produce recommendations. When
                ``None``, adapter-dependent operations raise
                :class:`NotImplementedError`.
        """
        self._repo = repository
        self._adapter = adapter

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

    def generate_initial_design(
        self, run_id: str, actor: str
    ) -> RecommendationBatch:
        """Generate and persist a run's first-round design in one transaction (§4.1).

        The service validates the pinned revision and policy, asks the adapter for
        the initial design, validates the returned candidates, and — atomically —
        persists the batch, opens the round, files one ``Pending`` experiment per
        candidate, advances the round counter, and transitions the run to
        ``RecommendationsPending``. Any failure rolls the whole step back.

        Args:
            run_id: The run to generate the initial design for.
            actor: The identity recorded in the decision log.

        Returns:
            The persisted :class:`RecommendationBatch`.

        Raises:
            NotImplementedError: If no optimizer adapter is configured.
            ServiceError: If the run/revision is unknown, the design space is
                invalid, or the adapter's candidates are invalid or the wrong
                count.
            StateTransitionError: If the run is not ``DesignSpaceValidated``.
        """
        if self._adapter is None:
            raise NotImplementedError(
                "generate_initial_design requires an optimizer adapter."
            )
        with self._repo.transaction():
            run = self._require_run(run_id)
            self._assert_initial_design_preconditions(run)
            revision = self._repo.get_revision(run.definition_revision_id)
            if revision is None:
                raise ServiceError(
                    f"Run {run_id!r} pins unknown revision "
                    f"{run.definition_revision_id!r}."
                )
            definition_result = validate_definition(revision)
            if not definition_result.ok:
                raise ServiceError(
                    "Cannot generate an initial design for an invalid design "
                    f"space: {self._codes(definition_result)}."
                )
            policy = run.optimization_policy
            result = self._adapter.generate_initial_design(revision, policy)
            candidates = list(result.candidates)
            if len(candidates) != policy.batch_size:
                raise ServiceError(
                    f"Adapter returned {len(candidates)} candidates, but the "
                    f"policy batchSize is {policy.batch_size}."
                )
            candidate_result = validate_candidates(revision, candidates)
            if not candidate_result.ok:
                raise ServiceError(
                    "Adapter returned invalid candidates: "
                    f"{self._codes(candidate_result)}."
                )
            round_number = run.round + 1
            now = _now()
            batch = RecommendationBatch(
                id=str(uuid.uuid4()),
                campaign_run_id=run_id,
                round_number=round_number,
                generated_at=now,
                input_snapshot=result.input_snapshot,
                algorithm_config=result.algorithm_config,
                candidates=candidates,
                status=BatchStatus.PROPOSED,
            )
            self._repo.add_batch(batch)
            experiment_round = ExperimentRound(
                id=str(uuid.uuid4()),
                campaign_run_id=run_id,
                round_number=round_number,
                recommendation_batch_id=batch.id,
                opened_at=now,
                status=RoundStatus.OPEN,
            )
            self._repo.add_round(experiment_round)
            for candidate in candidates:
                self._repo.add_experiment_run(
                    ExperimentRun(
                        id=str(uuid.uuid4()),
                        campaign_run_id=run_id,
                        experiment_round_id=experiment_round.id,
                        recommendation_candidate_id=candidate.id,
                        parameter_values=dict(candidate.parameter_values),
                        status=ExperimentRunStatus.PENDING,
                    )
                )
            self._transition(
                run,
                RunEvent.GENERATE_INITIAL_DESIGN,
                actor,
                DecisionAction.INITIAL_DESIGN_GENERATED,
                extra_update={"round": round_number},
            )
            return batch

    def recommend(self, run_id: str, actor: str) -> None:
        """Not available until a recommend-capable adapter exists (§4.1).

        Later-round recommendation must feed the run's history to the adapter and
        persist the batch, round, and transition atomically. That is out of scope
        for the single-round loop, so this operation is intentionally unavailable.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "recommend requires a recommend-capable optimizer adapter; it must "
            "create the batch, round, and transition atomically and is out of "
            "scope for the single-round loop."
        )

    def record_experiment_result(
        self,
        run_id: str,
        experiment_run_id: str,
        actor: str,
        status: ExperimentRunStatus,
        executed_at: datetime | None = None,
        notes: str | None = None,
    ) -> ExperimentRun:
        """Record the physical outcome of one experiment (§2.11).

        Only the execution status and its metadata may change; the candidate and
        parameter values are fixed at creation and must still match the batch.
        Recording a terminal result re-syncs the run's ``budgetUsed`` and the
        batch's execution status.

        Args:
            run_id: The run that owns the experiment.
            experiment_run_id: The experiment to update.
            actor: The executor identity, recorded on the run and in the log.
            status: The terminal execution status (``Completed`` or ``Failed``).
            executed_at: The execution timestamp; defaults to now.
            notes: Optional free-text notes.

        Returns:
            The saved experiment run.

        Raises:
            ServiceError: If the experiment is unknown, not owned by the run, not
                given a terminal status, or its candidate/values disagree with the
                batch.
        """
        if status not in _BUDGET_CONSUMING:
            raise ServiceError(
                "record_experiment_result only records terminal results "
                "(Completed/Failed); use abort_round to cancel."
            )
        with self._repo.transaction():
            run = self._require_run(run_id)
            if run.status is not RunStatus.RECOMMENDATIONS_PENDING:
                raise ServiceError(
                    "Experiment results may only be recorded while the run is "
                    f"RecommendationsPending; run {run_id!r} is "
                    f"{run.status.value!r}."
                )
            experiment = self._repo.get_experiment_run(experiment_run_id)
            if experiment is None:
                raise ServiceError(
                    f"Unknown experiment run {experiment_run_id!r}."
                )
            if experiment.campaign_run_id != run_id:
                raise ServiceError(
                    f"Experiment run {experiment_run_id!r} does not belong to run "
                    f"{run_id!r}."
                )
            experiment_round = self._repo.get_round(experiment.experiment_round_id)
            if experiment_round is None:
                raise ServiceError(
                    "Cannot resolve the round for experiment run "
                    f"{experiment_run_id!r}."
                )
            if experiment_round.status is not RoundStatus.OPEN:
                raise ServiceError(
                    f"Round {experiment_round.id!r} is closed; its experiment "
                    "results are frozen."
                )
            if experiment.status is not ExperimentRunStatus.PENDING:
                raise ServiceError(
                    f"Experiment run {experiment_run_id!r} is already "
                    f"{experiment.status.value!r}; a terminal result cannot be "
                    "overwritten."
                )
            self._assert_candidate_consistent(experiment)
            updated = experiment.model_copy(
                update={
                    "status": status,
                    "executed_at": executed_at or _now(),
                    "executed_by": actor,
                    "notes": notes if notes is not None else experiment.notes,
                }
            )
            self._repo.save_experiment_run(updated)
            self._sync_batch_status(experiment.experiment_round_id)
            self._sync_budget_used(run)
            self._repo.append_decision_log(
                DecisionLog(
                    id=str(uuid.uuid4()),
                    campaign_run_id=run_id,
                    timestamp=_now(),
                    actor=actor,
                    action=DecisionAction.EXPERIMENT_RUN_EXECUTED,
                    definition_revision_id=run.definition_revision_id,
                    related_entity_id=experiment_run_id,
                )
            )
            return updated

    def record_measurement(
        self, measurement: Measurement, actor: str
    ) -> Measurement:
        """Append a measurement to its supersede chain and log it (§2.12).

        The repository validates that the output belongs to the run's pinned
        revision and that the reading extends the ``(experimentRunId, outputId)``
        chain contiguously; the service adds the audit entry.

        Args:
            measurement: The reading to append.
            actor: The recorder identity for the decision log.

        Returns:
            The appended measurement.

        Raises:
            ServiceError: If the experiment run or its campaign run is unknown.
            PersistenceError: If the output or supersede chain is invalid.
        """
        if measurement.recorded_by != actor:
            raise ServiceError(
                "measurement.recordedBy must equal the recording actor "
                f"({measurement.recorded_by!r} != {actor!r})."
            )
        with self._repo.transaction():
            experiment = self._repo.get_experiment_run(measurement.experiment_run_id)
            if experiment is None:
                raise ServiceError(
                    "Measurement references unknown experiment run "
                    f"{measurement.experiment_run_id!r}."
                )
            run = self._repo.get_run(experiment.campaign_run_id)
            if run is None:
                raise ServiceError(
                    "Cannot resolve the campaign run for experiment run "
                    f"{experiment.id!r}."
                )
            if run.status not in _MEASURABLE_STATUSES:
                raise ServiceError(
                    "Measurements may only be recorded while the run is "
                    "RecommendationsPending or AwaitingMeasurements; run "
                    f"{run.id!r} is {run.status.value!r}."
                )
            experiment_round = self._repo.get_round(experiment.experiment_round_id)
            if experiment_round is None:
                raise ServiceError(
                    "Cannot resolve the round for experiment run "
                    f"{experiment.id!r}."
                )
            if experiment_round.status is not RoundStatus.OPEN:
                raise ServiceError(
                    f"Round {experiment_round.id!r} is closed; measurements are "
                    "frozen."
                )
            if experiment.status is not ExperimentRunStatus.COMPLETED:
                raise ServiceError(
                    "Measurements may only be recorded for Completed experiments; "
                    f"experiment {experiment.id!r} is {experiment.status.value!r}."
                )
            self._repo.add_measurement(measurement)
            action = (
                DecisionAction.MEASUREMENT_RECORDED
                if measurement.revision == 1
                else DecisionAction.MEASUREMENT_SUPERSEDED
            )
            self._repo.append_decision_log(
                DecisionLog(
                    id=str(uuid.uuid4()),
                    campaign_run_id=run.id,
                    timestamp=_now(),
                    actor=actor,
                    action=action,
                    definition_revision_id=run.definition_revision_id,
                    related_entity_id=measurement.id,
                )
            )
            return measurement

    def mark_all_runs_terminal(self, run_id: str, actor: str) -> CampaignRun:
        """Move a pending round to awaiting-measurements once execution ends (§3.2).

        The run may only leave ``RecommendationsPending`` when its open round's
        experiments correspond one-to-one with the batch's candidates and every
        one of them is physically finished (Completed/Failed/Cancelled).

        Args:
            run_id: The run to advance.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in the ``AwaitingMeasurements`` state.

        Raises:
            ServiceError: If there is no open round, the experiments do not match
                the batch candidates, or one is still pending.
            StateTransitionError: If the transition is not permitted.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            open_round = self._current_open_round(run_id)
            if open_round is None:
                raise ServiceError(
                    f"Run {run_id!r} has no open round to conclude."
                )
            batch = self._repo.get_batch(open_round.recommendation_batch_id)
            if batch is None:
                raise ServiceError(
                    "Cannot resolve the recommendation batch for round "
                    f"{open_round.id!r}."
                )
            experiments = self._repo.list_experiment_runs(open_round.id)
            if not experiments:
                raise ServiceError(
                    f"Round {open_round.id!r} has no experiments to conclude."
                )
            candidate_ids = {candidate.id for candidate in batch.candidates}
            experiment_candidate_ids = {
                experiment.recommendation_candidate_id for experiment in experiments
            }
            if experiment_candidate_ids != candidate_ids or len(experiments) != len(
                batch.candidates
            ):
                raise ServiceError(
                    "The round's experiments must correspond one-to-one with the "
                    "batch's candidates before it can be concluded."
                )
            not_terminal = [
                experiment.id
                for experiment in experiments
                if experiment.status not in _TERMINAL_EXPERIMENT
            ]
            if not_terminal:
                raise ServiceError(
                    "Cannot mark all runs terminal while experiments are not "
                    f"finished: {not_terminal}."
                )
            return self._transition(run, RunEvent.ALL_RUNS_TERMINAL, actor, None)

    def close_round(self, run_id: str, round_id: str, actor: str) -> CampaignRun:
        """Close a run's open round once its results are ready (§3.2, §4).

        Readiness is judged by :func:`assess_readiness` over the round's
        measurements — the objective's outputs must all have a valid reading —
        never asserted by the caller. A round that is not ready cannot be closed
        (use :meth:`abort_round` to abandon it instead).

        Args:
            run_id: The run that owns the round.
            round_id: The round to close.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run in the ``RoundClosed`` state.

        Raises:
            ServiceError: If the round does not exist, belongs to another run, is
                not open, or is not ready to close.
            StateTransitionError: If the run cannot close a round now.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            experiment_round = self._require_round(round_id, run_id)
            if experiment_round.status is not RoundStatus.OPEN:
                raise ServiceError(f"Round {round_id!r} is already closed.")
            revision = self._repo.get_revision(run.definition_revision_id)
            if revision is None:
                raise ServiceError(
                    f"Run {run_id!r} pins unknown revision "
                    f"{run.definition_revision_id!r}."
                )
            experiments = self._repo.list_experiment_runs(round_id)
            measurements: list[Measurement] = []
            for experiment in experiments:
                measurements.extend(self._repo.list_measurements(experiment.id))
            readiness = assess_readiness(revision, experiments, measurements)
            if not readiness.ready:
                raise ServiceError(
                    f"Round {round_id!r} is not ready to close: "
                    f"{self._codes(readiness)}."
                )
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

        The policy is editable only while the run is still ``Draft`` or
        ``DesignSpaceValidated`` and no batch has been generated; an archived or
        in-flight run is frozen.

        Args:
            run_id: The run to update.
            policy: The replacement policy.

        Returns:
            The saved run.

        Raises:
            ServiceError: If the run does not exist, is not in an editable state,
                or a batch already exists.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            self._require_editable(run)
            self._reject_if_batched(run_id, "optimizationPolicy")
            updated = run.model_copy(
                update={"optimization_policy": policy, "updated_at": _now()}
            )
            self._repo.save_run(updated)
            return updated

    def repin_revision(
        self, run_id: str, revision_id: str, actor: str
    ) -> CampaignRun:
        """Repin a run's definition revision before its first batch (§3.6).

        The run must still be editable (``Draft``/``DesignSpaceValidated``) with
        no batch yet, and the target revision must exist and belong to the run's
        campaign. Repinning to a *different* revision invalidates any prior
        validation, so a ``DesignSpaceValidated`` run drops back to ``Draft``.

        Args:
            run_id: The run to update.
            revision_id: The revision id to pin.
            actor: The identity recorded in the decision log.

        Returns:
            The saved run.

        Raises:
            ServiceError: If the run does not exist, is not editable, a batch
                already exists, or the target revision is unknown or foreign.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            self._require_editable(run)
            self._reject_if_batched(run_id, "definitionRevisionId")
            revision = self._repo.get_revision(revision_id)
            if revision is None:
                raise ServiceError(f"Unknown revision {revision_id!r}.")
            if revision.campaign_definition_id != run.campaign_definition_id:
                raise ServiceError(
                    "The target revision must belong to the run's campaign "
                    "definition."
                )
            changed = revision_id != run.definition_revision_id
            update: dict[str, Any] = {
                "definition_revision_id": revision_id,
                "updated_at": _now(),
            }
            if changed and run.status is RunStatus.DESIGN_SPACE_VALIDATED:
                update["status"] = RunStatus.DRAFT
            updated = run.model_copy(update=update)
            self._repo.save_run(updated)
            self._repo.append_decision_log(
                DecisionLog(
                    id=str(uuid.uuid4()),
                    campaign_run_id=run_id,
                    timestamp=_now(),
                    actor=actor,
                    action=DecisionAction.REVISION_REPINNED,
                    definition_revision_id=revision_id,
                )
            )
            return updated

    def recompute_counters(self, run_id: str) -> CampaignRun:
        """Derive ``round`` and ``budgetUsed`` from persisted entities (§3.5).

        ``round`` is the number of persisted recommendation batches; ``budgetUsed``
        is the number of experiment runs in a budget-consuming (Completed/Failed)
        status.

        Args:
            run_id: The run whose counters to recompute.

        Returns:
            The saved run with reconciled counters.

        Raises:
            ServiceError: If the run does not exist.
        """
        with self._repo.transaction():
            run = self._require_run(run_id)
            batches = self._repo.list_batches(run_id)
            experiments = self._repo.list_experiment_runs_for_run(run_id)
            consumed = sum(
                1 for e in experiments if e.status in _BUDGET_CONSUMING
            )
            updated = run.model_copy(
                update={
                    "round": len(batches),
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

    def _assert_initial_design_preconditions(self, run: CampaignRun) -> None:
        """Guard the initial-design gate so the adapter is only called when valid.

        Every precondition is checked before the optimizer adapter is invoked: a
        failure here means the adapter is never called and nothing is persisted.
        """
        if run.status is not RunStatus.DESIGN_SPACE_VALIDATED:
            raise ServiceError(
                f"generate_initial_design requires a DesignSpaceValidated run; "
                f"run {run.id!r} is {run.status.value!r}."
            )
        if run.round != 0:
            raise ServiceError(
                f"generate_initial_design is only for the first round; run "
                f"{run.id!r} already has round={run.round}."
            )
        if self._repo.list_batches(run.id):
            raise ServiceError(
                f"Run {run.id!r} already has a recommendation batch; the initial "
                "design has already been generated."
            )
        open_rounds = [
            experiment_round
            for experiment_round in self._repo.list_rounds(run.id)
            if experiment_round.status is RoundStatus.OPEN
        ]
        if open_rounds:
            raise ServiceError(
                f"Run {run.id!r} has an open round "
                f"{[r.id for r in open_rounds]}; cannot generate an initial design."
            )
        batch_size = run.optimization_policy.batch_size
        remaining = run.budget_total - run.budget_used
        if batch_size > remaining:
            raise ServiceError(
                f"Policy batchSize {batch_size} exceeds the remaining budget "
                f"{remaining} (budgetTotal={run.budget_total}, "
                f"budgetUsed={run.budget_used})."
            )

    def _assert_candidate_consistent(self, experiment: ExperimentRun) -> None:
        """Assert an experiment's candidate/values still match its batch (§2.11)."""
        experiment_round = self._repo.get_round(experiment.experiment_round_id)
        if experiment_round is None:
            raise ServiceError(
                "Cannot resolve the round for experiment run "
                f"{experiment.id!r}."
            )
        batch = self._repo.get_batch(experiment_round.recommendation_batch_id)
        if batch is None:
            raise ServiceError(
                "Cannot resolve the recommendation batch for round "
                f"{experiment_round.id!r}."
            )
        candidate = next(
            (
                candidate
                for candidate in batch.candidates
                if candidate.id == experiment.recommendation_candidate_id
            ),
            None,
        )
        if candidate is None:
            raise ServiceError(
                f"Experiment run {experiment.id!r} references candidate "
                f"{experiment.recommendation_candidate_id!r}, which is not in its "
                "batch."
            )
        if dict(candidate.parameter_values) != dict(experiment.parameter_values):
            raise ServiceError(
                f"Experiment run {experiment.id!r} parameter values disagree with "
                f"its recommendation candidate {candidate.id!r}."
            )

    def _sync_batch_status(self, round_id: str) -> None:
        """Reconcile a batch's status with how many experiments have executed."""
        experiment_round = self._repo.get_round(round_id)
        if experiment_round is None:
            return
        batch = self._repo.get_batch(experiment_round.recommendation_batch_id)
        if batch is None or batch.status is BatchStatus.SUPERSEDED:
            return
        experiments = self._repo.list_experiment_runs(round_id)
        executed = sum(1 for e in experiments if e.status in _BUDGET_CONSUMING)
        if executed == 0:
            status = BatchStatus.PROPOSED
        elif executed >= len(batch.candidates):
            status = BatchStatus.FULLY_EXECUTED
        else:
            status = BatchStatus.PARTIALLY_EXECUTED
        if status is not batch.status:
            self._repo.save_batch(batch.model_copy(update={"status": status}))

    def _sync_budget_used(self, run: CampaignRun) -> None:
        """Reconcile a run's ``budgetUsed`` with its terminal experiment count."""
        experiments = self._repo.list_experiment_runs_for_run(run.id)
        consumed = sum(1 for e in experiments if e.status in _BUDGET_CONSUMING)
        if consumed != run.budget_used:
            self._repo.save_run(
                run.model_copy(
                    update={"budget_used": consumed, "updated_at": _now()}
                )
            )

    def _require_run(self, run_id: str) -> CampaignRun:
        """Fetch a run or raise a :class:`ServiceError`."""
        run = self._repo.get_run(run_id)
        if run is None:
            raise ServiceError(f"Unknown run {run_id!r}.")
        return run

    def _require_editable(self, run: CampaignRun) -> None:
        """Reject an edit to a run that is past the editable states (§3.6)."""
        if run.status not in _EDITABLE_STATUSES:
            raise ServiceError(
                f"Run {run.id!r} is not editable in state {run.status.value!r}; "
                "only Draft or DesignSpaceValidated runs may be edited."
            )

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

    @staticmethod
    def _codes(result: ValidationResult) -> list[str]:
        """Return the blocking issue codes of a validation result."""
        return [issue.code for issue in result.blocking_issues]


__all__ = ["ApplicationService", "ServiceError", "PersistenceError"]

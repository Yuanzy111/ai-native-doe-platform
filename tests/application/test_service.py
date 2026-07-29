"""Application-service counterexamples for state, ownership, and freeze (§3).

These tests pin down the intent-explicit service API: there is no generic
``transition`` escape hatch, the adapter-dependent operations are gated, and each
named operation performs the real work (validation, round closure, cancellation)
behind its state transition rather than trusting the caller.
"""

import pytest

from backend.application import ServiceError
from backend.domain import models as m
from backend.domain.validation import StateTransitionError


class TestCreateCampaign:
    """Definition + first revision are created atomically and consistently."""

    def test_create_sets_head_to_first_revision(
        self, service, repo, make_definition, make_revision
    ):
        service.create_campaign(make_definition(), make_revision())
        assert repo.get_definition("cd-1").head_revision_id == "rev-1"

    def test_head_must_point_at_first_revision(
        self, service, make_definition, make_revision
    ):
        with pytest.raises(ServiceError):
            service.create_campaign(
                make_definition(head_revision_id="other"), make_revision()
            )

    def test_create_is_atomic_on_name_collision(
        self, service, repo, make_definition, make_revision
    ):
        service.create_campaign(make_definition(), make_revision())
        # A second campaign reusing the unique name must roll the revision back.
        with pytest.raises(Exception):
            service.create_campaign(
                make_definition(id="cd-2", head_revision_id="rev-2"),
                make_revision(id="rev-2", campaign_definition_id="cd-2"),
            )
        assert repo.get_revision("rev-2") is None


class TestAddRevision:
    """Appending a revision advances the definition head in one transaction."""

    def test_add_revision_advances_head(
        self, service, repo, make_definition, make_revision
    ):
        service.create_campaign(make_definition(), make_revision())
        service.add_revision(
            make_revision(id="rev-2", revision_number=2, parent_revision_id="rev-1")
        )
        assert repo.get_definition("cd-1").head_revision_id == "rev-2"


class TestCreateRun:
    """A new run must start clean and pin a revision of its own campaign (§3.1)."""

    def test_create_run_persists_draft(self, service, seeded_definition, make_run):
        service.create_run(make_run())
        assert seeded_definition.get_run("run-1").status is m.RunStatus.DRAFT

    def test_create_run_rejects_non_draft(
        self, service, seeded_definition, make_run
    ):
        with pytest.raises(ServiceError):
            service.create_run(make_run(status=m.RunStatus.DESIGN_SPACE_VALIDATED))

    def test_create_run_rejects_started_round(
        self, service, seeded_definition, make_run
    ):
        with pytest.raises(ServiceError):
            service.create_run(make_run(round=1))

    def test_create_run_rejects_used_budget(
        self, service, seeded_definition, make_run
    ):
        with pytest.raises(ServiceError):
            service.create_run(make_run(budget_used=1))

    def test_create_run_rejects_unknown_revision(
        self, service, seeded_definition, make_run
    ):
        with pytest.raises(ServiceError):
            service.create_run(make_run(definition_revision_id="ghost"))

    def test_create_run_rejects_foreign_revision(
        self, service, seeded_definition, repo, make_definition, make_revision, make_run
    ):
        with repo.transaction():
            repo.add_definition(make_definition(id="cd-2", name="Other", head_revision_id="rev-2"))
            repo.add_revision(make_revision(id="rev-2", campaign_definition_id="cd-2"))
        with pytest.raises(ServiceError):
            service.create_run(make_run(definition_revision_id="rev-2"))


class TestValidateDesignSpace:
    """Validation is performed by the service, not declared by the caller (§4)."""

    def test_valid_definition_advances_to_validated(self, service, seeded_run):
        result = service.validate_design_space("run-1", "user-1")
        assert result.ok
        assert seeded_run.get_run("run-1").status is m.RunStatus.DESIGN_SPACE_VALIDATED
        logs = seeded_run.list_decision_logs("run-1")
        assert [log.action for log in logs] == [m.DecisionAction.DESIGN_SPACE_VALIDATED]

    def test_invalid_definition_stays_draft(
        self, service, seeded_definition, make_revision, make_run
    ):
        service.add_revision(
            make_revision(
                id="rev-2",
                revision_number=2,
                parent_revision_id="rev-1",
                constraints_confirmed=False,
                constraints_confirmed_at=None,
            )
        )
        service.create_run(make_run(definition_revision_id="rev-2"))
        result = service.validate_design_space("run-1", "user-1")
        assert not result.ok
        assert seeded_definition.get_run("run-1").status is m.RunStatus.DRAFT
        logs = seeded_definition.list_decision_logs("run-1")
        assert [log.action for log in logs] == [
            m.DecisionAction.DESIGN_SPACE_VALIDATION_FAILED
        ]

    def test_unknown_run_raises(self, service):
        with pytest.raises(ServiceError):
            service.validate_design_space("ghost", "user-1")


class TestAdapterGated:
    """Adapter-dependent operations and the generic escape hatch are unavailable."""

    def test_no_generic_transition(self, service):
        assert not hasattr(service, "transition")

    def test_generate_initial_design_not_implemented(self, service, seeded_run):
        with pytest.raises(NotImplementedError):
            service.generate_initial_design("run-1", "user-1")

    def test_recommend_not_implemented(self, service, seeded_run):
        with pytest.raises(NotImplementedError):
            service.recommend("run-1", "user-1")


class TestRepinRevision:
    """Repinning verifies existence and campaign ownership, and freezes (§3.6)."""

    def _add_second_revision(self, service, make_revision):
        service.add_revision(
            make_revision(id="rev-2", revision_number=2, parent_revision_id="rev-1")
        )

    def test_repin_to_valid_revision(self, service, seeded_run, make_revision):
        self._add_second_revision(service, make_revision)
        run = service.repin_revision("run-1", "rev-2")
        assert run.definition_revision_id == "rev-2"

    def test_repin_unknown_revision_rejected(self, service, seeded_run):
        with pytest.raises(ServiceError):
            service.repin_revision("run-1", "ghost")

    def test_repin_foreign_revision_rejected(
        self, service, seeded_run, repo, make_definition, make_revision
    ):
        with repo.transaction():
            repo.add_definition(
                make_definition(id="cd-2", name="Other", head_revision_id="rev-x")
            )
            repo.add_revision(make_revision(id="rev-x", campaign_definition_id="cd-2"))
        with pytest.raises(ServiceError):
            service.repin_revision("run-1", "rev-x")

    def test_repin_frozen_after_batch(self, service, seeded_run, make_batch):
        seeded_run.add_batch(make_batch())
        with pytest.raises(ServiceError):
            service.repin_revision("run-1", "rev-2")


class TestFreezeAfterFirstBatch:
    """Policy is frozen once a batch exists (§3.6)."""

    def _new_policy(self) -> m.OptimizationPolicy:
        return m.OptimizationPolicy(
            id="op-2",
            batch_size=8,
            seed_policy=m.SeedPolicy.AUTO_GENERATED,
            strategy_config=m.BotorchConfig(acquisition_function="qLogEI"),
        )

    def test_policy_editable_before_batch(self, service, seeded_run):
        run = service.update_policy("run-1", self._new_policy())
        assert run.optimization_policy.batch_size == 8

    def test_policy_frozen_after_batch(self, service, seeded_run, make_batch):
        seeded_run.add_batch(make_batch())
        with pytest.raises(ServiceError):
            service.update_policy("run-1", self._new_policy())


def _seed_run_with_round(
    repo,
    make_definition,
    make_revision,
    make_run,
    make_batch,
    make_round,
    *,
    status,
    round_status=m.RoundStatus.OPEN,
):
    """Seed ``cd-1``/``rev-1`` plus a run in ``status`` with one round + batch."""
    with repo.transaction():
        repo.add_definition(make_definition())
        repo.add_revision(make_revision())
        repo.add_run(make_run(status=status))
        repo.add_batch(make_batch(id="batch-1", round_number=1))
        repo.add_round(
            make_round(
                id="round-1",
                round_number=1,
                recommendation_batch_id="batch-1",
                status=round_status,
            )
        )
    return repo


class TestCloseRound:
    """Closing a round advances the run and syncs the round in one transaction."""

    def test_close_round_syncs_round_status(
        self, service, repo, make_definition, make_revision, make_run, make_batch, make_round
    ):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.AWAITING_MEASUREMENTS,
        )
        run = service.close_round("run-1", "round-1", "user-1")
        assert run.status is m.RunStatus.ROUND_CLOSED
        closed = repo.get_round("round-1")
        assert closed.status is m.RoundStatus.CLOSED
        assert closed.closed_at is not None
        logs = repo.list_decision_logs("run-1")
        assert m.DecisionAction.ROUND_CLOSED in [log.action for log in logs]

    def test_close_unknown_round_rejected(
        self, service, repo, make_definition, make_revision, make_run, make_batch, make_round
    ):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.AWAITING_MEASUREMENTS,
        )
        with pytest.raises(ServiceError):
            service.close_round("run-1", "ghost", "user-1")

    def test_close_foreign_round_rejected(
        self, service, repo, make_definition, make_revision, make_run, make_batch, make_round
    ):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.AWAITING_MEASUREMENTS,
        )
        with repo.transaction():
            repo.add_run(make_run(id="run-2", status=m.RunStatus.AWAITING_MEASUREMENTS))
            repo.add_batch(make_batch(id="batch-2", campaign_run_id="run-2", round_number=1))
            repo.add_round(
                make_round(
                    id="round-2",
                    campaign_run_id="run-2",
                    round_number=1,
                    recommendation_batch_id="batch-2",
                )
            )
        with pytest.raises(ServiceError):
            service.close_round("run-1", "round-2", "user-1")

    def test_close_illegal_from_draft_raises(
        self, service, repo, make_definition, make_revision, make_run, make_batch, make_round
    ):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.DRAFT,
        )
        with pytest.raises(StateTransitionError):
            service.close_round("run-1", "round-1", "user-1")


class TestAbortRound:
    """Aborting cancels pending experiments, closes the round, supersedes batch."""

    def test_abort_cancels_pending_and_supersedes_batch(
        self,
        service,
        repo,
        make_definition,
        make_revision,
        make_run,
        make_batch,
        make_round,
        make_experiment_run,
    ):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.RECOMMENDATIONS_PENDING,
        )
        with repo.transaction():
            repo.add_experiment_run(
                make_experiment_run(
                    id="exp-1",
                    status=m.ExperimentRunStatus.PENDING,
                    executed_at=None,
                    executed_by=None,
                )
            )
        run = service.abort_round("run-1", "round-1", "user-1")
        assert run.status is m.RunStatus.ROUND_CLOSED
        assert repo.get_experiment_run("exp-1").status is m.ExperimentRunStatus.CANCELLED
        assert repo.get_round("round-1").status is m.RoundStatus.CLOSED
        assert repo.get_batch("batch-1").status is m.BatchStatus.SUPERSEDED
        logs = repo.list_decision_logs("run-1")
        assert m.DecisionAction.ROUND_ABORTED in [log.action for log in logs]


class TestMarkAllRunsTerminal:
    """Concluding a pending round requires every experiment to be terminal."""

    def test_pending_experiment_blocks(
        self, service, repo, make_definition, make_revision, make_run, make_batch, make_round, make_experiment_run
    ):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.RECOMMENDATIONS_PENDING,
        )
        with repo.transaction():
            repo.add_experiment_run(
                make_experiment_run(
                    id="exp-1",
                    status=m.ExperimentRunStatus.PENDING,
                    executed_at=None,
                    executed_by=None,
                )
            )
        with pytest.raises(ServiceError):
            service.mark_all_runs_terminal("run-1", "user-1")

    def test_advances_when_all_terminal(
        self, service, repo, make_definition, make_revision, make_run, make_batch, make_round, make_experiment_run
    ):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.RECOMMENDATIONS_PENDING,
        )
        with repo.transaction():
            repo.add_experiment_run(make_experiment_run(id="exp-1"))
        run = service.mark_all_runs_terminal("run-1", "user-1")
        assert run.status is m.RunStatus.AWAITING_MEASUREMENTS

    def test_no_open_round_rejected(self, service, repo, make_definition, make_revision, make_run, make_batch, make_round):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.RECOMMENDATIONS_PENDING,
            round_status=m.RoundStatus.CLOSED,
        )
        with pytest.raises(ServiceError):
            service.mark_all_runs_terminal("run-1", "user-1")


class TestMarkCompleted:
    """A run may only complete when no round remains open."""

    def test_completes_when_no_open_round(
        self, service, repo, make_definition, make_revision, make_run, make_batch, make_round
    ):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.ROUND_CLOSED,
            round_status=m.RoundStatus.CLOSED,
        )
        run = service.mark_completed("run-1", "user-1")
        assert run.status is m.RunStatus.COMPLETED
        logs = repo.list_decision_logs("run-1")
        assert m.DecisionAction.RUN_COMPLETED in [log.action for log in logs]

    def test_open_round_blocks(self, service, repo, make_definition, make_revision, make_run, make_batch, make_round):
        _seed_run_with_round(
            repo, make_definition, make_revision, make_run, make_batch, make_round,
            status=m.RunStatus.ROUND_CLOSED,
            round_status=m.RoundStatus.OPEN,
        )
        with pytest.raises(ServiceError):
            service.mark_completed("run-1", "user-1")


class TestRecomputeCounters:
    """round and budgetUsed are derived from persisted entities (§3.5)."""

    def test_counters_are_derived(
        self, service, seeded_run, make_batch, make_round, make_experiment_run
    ):
        repo = seeded_run
        repo.add_batch(make_batch(id="batch-1", round_number=1))
        repo.add_round(
            make_round(
                id="round-1",
                round_number=1,
                recommendation_batch_id="batch-1",
                status=m.RoundStatus.CLOSED,
            )
        )
        repo.add_experiment_run(
            make_experiment_run(id="e1", status=m.ExperimentRunStatus.COMPLETED)
        )
        repo.add_experiment_run(
            make_experiment_run(
                id="e2",
                status=m.ExperimentRunStatus.FAILED,
                executed_at="2026-07-29T01:00:00Z",
                executed_by="user-1",
            )
        )
        repo.add_experiment_run(
            make_experiment_run(
                id="e3",
                status=m.ExperimentRunStatus.PENDING,
                executed_at=None,
                executed_by=None,
            )
        )
        run = service.recompute_counters("run-1")
        assert (run.round, run.budget_used) == (1, 2)

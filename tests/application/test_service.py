"""Application-service counterexamples: state changes, freeze, derived counters (§3)."""

import pytest

from backend.application import ServiceError
from backend.domain import models as m
from backend.domain.validation import RunEvent, StateTransitionError


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


class TestTransition:
    """Status changes only through RunEvent and the §3.2 state machine."""

    def test_legal_transition_updates_status(self, service, seeded_run):
        run = service.transition("run-1", RunEvent.VALIDATE_DEFINITION_PASS, "user-1")
        assert run.status is m.RunStatus.DESIGN_SPACE_VALIDATED

    def test_legal_transition_appends_decision_log(self, service, seeded_run):
        service.transition("run-1", RunEvent.VALIDATE_DEFINITION_PASS, "user-1")
        logs = seeded_run.list_decision_logs("run-1")
        assert [log.action for log in logs] == [
            m.DecisionAction.DESIGN_SPACE_VALIDATED
        ]

    def test_illegal_transition_raises(self, service, seeded_run):
        with pytest.raises(StateTransitionError):
            service.transition("run-1", RunEvent.RECOMMEND, "user-1")

    def test_unknown_run_raises(self, service):
        with pytest.raises(ServiceError):
            service.transition("ghost", RunEvent.ARCHIVE, "user-1")


class TestFreezeAfterFirstBatch:
    """Policy and pinned revision are frozen once a batch exists (§3.6)."""

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

    def test_revision_repin_frozen_after_batch(self, service, seeded_run, make_batch):
        seeded_run.add_batch(make_batch())
        with pytest.raises(ServiceError):
            service.repin_revision("run-1", "rev-2")


class TestRecomputeCounters:
    """round and budgetUsed are derived from persisted entities (§3.5)."""

    def test_counters_are_derived(
        self, service, seeded_run, make_round, make_experiment_run
    ):
        repo = seeded_run
        repo.add_round(
            make_round(id="round-1", round_number=1, status=m.RoundStatus.CLOSED)
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

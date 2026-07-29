"""The backend single-round experiment loop, exercised end-to-end (§4.1).

These tests drive the closed loop through a test-only ``FakeOptimizerAdapter``:
generate the initial design, record each experiment's outcome, take measurements,
and close the round — plus the counterexamples and transaction-rollback cases the
service must reject. No production optimizer (BayBE/BoFire) is involved.
"""

import pytest

from backend.application import ApplicationService, ServiceError
from backend.application.adapter import RecommendationResult
from backend.domain import models as m
from backend.domain.validation import StateTransitionError
from backend.persistence import PersistenceError


def _validated_run(service, repo):
    """Advance the seeded Draft ``run-1`` to ``DesignSpaceValidated``."""
    service.validate_design_space("run-1", "user-1")
    return repo


class _CountAdapter:
    """An adapter that returns a wrong number of candidates."""

    def __init__(self, count: int) -> None:
        self._count = count

    def generate_initial_design(self, revision, policy):
        candidates = [
            m.RecommendationCandidate(
                id=f"cand-{i}",
                parameter_values={"resin": float(i), "hard": float(i)},
            )
            for i in range(1, self._count + 1)
        ]
        return RecommendationResult(
            candidates=candidates,
            algorithm_config=_config(policy),
        )


class _IllegalCandidateAdapter:
    """An adapter that returns an out-of-bounds candidate."""

    def generate_initial_design(self, revision, policy):
        candidates = [
            m.RecommendationCandidate(
                id="cand-1", parameter_values={"resin": 999.0, "hard": 1.0}
            )
        ]
        candidates += [
            m.RecommendationCandidate(
                id=f"cand-{i}",
                parameter_values={"resin": float(i), "hard": float(i)},
            )
            for i in range(2, policy.batch_size + 1)
        ]
        return RecommendationResult(
            candidates=candidates, algorithm_config=_config(policy)
        )


def _config(policy) -> m.AlgorithmConfig:
    return m.AlgorithmConfig(
        backend_name="fake",
        backend_version="0.0.0",
        backend_commit="cafef00d",
        strategy_kind="Botorch",
        acquisition_function="qLogEI",
        seed=policy.seed_value,
        environment=m.Environment(
            python_version="3.11.15",
            torch_version="2.4.0",
            botorch_version="0.11.0",
            dependency_lock_hash="sha256:0",
        ),
    )


class TestGenerateInitialDesign:
    """The initial design is validated, persisted, and transitioned atomically."""

    def test_happy_path_persists_batch_round_and_experiments(
        self, service_with_adapter, seeded_run, fake_adapter
    ):
        repo = _validated_run(service_with_adapter, seeded_run)
        batch = service_with_adapter.generate_initial_design("run-1", "user-1")

        run = repo.get_run("run-1")
        assert run.status is m.RunStatus.RECOMMENDATIONS_PENDING
        assert run.round == 1

        persisted = repo.get_batch(batch.id)
        assert persisted is not None
        assert persisted.status is m.BatchStatus.PROPOSED
        assert len(persisted.candidates) == run.optimization_policy.batch_size

        rounds = repo.list_rounds("run-1")
        assert len(rounds) == 1
        assert rounds[0].status is m.RoundStatus.OPEN

        experiments = repo.list_experiment_runs(rounds[0].id)
        assert len(experiments) == run.optimization_policy.batch_size
        assert all(e.status is m.ExperimentRunStatus.PENDING for e in experiments)
        assert {e.recommendation_candidate_id for e in experiments} == {
            c.id for c in persisted.candidates
        }

        assert fake_adapter.calls == [("rev-1", "op-1")]
        logs = repo.list_decision_logs("run-1")
        assert m.DecisionAction.INITIAL_DESIGN_GENERATED in [l.action for l in logs]

    def test_no_adapter_is_not_implemented(self, service, seeded_run):
        with pytest.raises(NotImplementedError):
            service.generate_initial_design("run-1", "user-1")

    def test_wrong_candidate_count_rejected(self, repo, seeded_run):
        service = ApplicationService(repo, adapter=_CountAdapter(count=2))
        _validated_run(service, repo)
        with pytest.raises(ServiceError):
            service.generate_initial_design("run-1", "user-1")

    def test_invalid_candidates_rejected(self, repo, seeded_run):
        service = ApplicationService(repo, adapter=_IllegalCandidateAdapter())
        _validated_run(service, repo)
        with pytest.raises(ServiceError):
            service.generate_initial_design("run-1", "user-1")

    def test_illegal_state_rolls_back_everything(
        self, service_with_adapter, seeded_run
    ):
        # A Draft (never-validated) run: the batch/round/experiments are written
        # inside the transaction, then the state transition fails and rolls back.
        repo = seeded_run
        with pytest.raises(StateTransitionError):
            service_with_adapter.generate_initial_design("run-1", "user-1")

        run = repo.get_run("run-1")
        assert run.status is m.RunStatus.DRAFT
        assert run.round == 0
        assert repo.list_batches("run-1") == []
        assert repo.list_rounds("run-1") == []
        assert repo.list_experiment_runs_for_run("run-1") == []

    def test_wrong_count_leaves_no_partial_state(self, repo, seeded_run):
        service = ApplicationService(repo, adapter=_CountAdapter(count=2))
        _validated_run(service, repo)
        with pytest.raises(ServiceError):
            service.generate_initial_design("run-1", "user-1")
        assert repo.list_batches("run-1") == []
        assert repo.list_rounds("run-1") == []
        assert repo.get_run("run-1").status is m.RunStatus.DESIGN_SPACE_VALIDATED


class TestRecordExperimentResult:
    """Only execution status/metadata change; counters and batch status re-sync."""

    def _generate(self, service, repo):
        service.validate_design_space("run-1", "user-1")
        service.generate_initial_design("run-1", "user-1")
        return repo.list_experiment_runs_for_run("run-1")

    def test_terminal_result_syncs_budget_and_batch(
        self, service_with_adapter, seeded_run
    ):
        repo = seeded_run
        experiments = self._generate(service_with_adapter, repo)
        service_with_adapter.record_experiment_result(
            "run-1", experiments[0].id, "user-1", m.ExperimentRunStatus.COMPLETED
        )
        assert repo.get_run("run-1").budget_used == 1
        batch = repo.list_batches("run-1")[0]
        assert repo.get_batch(batch.id).status is m.BatchStatus.PARTIALLY_EXECUTED

        for experiment in experiments[1:]:
            service_with_adapter.record_experiment_result(
                "run-1", experiment.id, "user-1", m.ExperimentRunStatus.COMPLETED
            )
        assert repo.get_run("run-1").budget_used == len(experiments)
        assert repo.get_batch(batch.id).status is m.BatchStatus.FULLY_EXECUTED

    def test_non_terminal_status_rejected(self, service_with_adapter, seeded_run):
        experiments = self._generate(service_with_adapter, seeded_run)
        with pytest.raises(ServiceError):
            service_with_adapter.record_experiment_result(
                "run-1", experiments[0].id, "user-1", m.ExperimentRunStatus.PENDING
            )

    def test_unknown_experiment_rejected(self, service_with_adapter, seeded_run):
        self._generate(service_with_adapter, seeded_run)
        with pytest.raises(ServiceError):
            service_with_adapter.record_experiment_result(
                "run-1", "ghost", "user-1", m.ExperimentRunStatus.COMPLETED
            )

    def test_candidate_value_mismatch_rejected(
        self, service, seeded_run, make_batch, make_round, make_experiment_run
    ):
        repo = seeded_run
        with repo.transaction():
            repo.add_batch(make_batch())  # cand-1 -> {resin: 60, hard: 40}
            repo.add_round(make_round())
            repo.add_experiment_run(
                make_experiment_run(
                    id="exp-x",
                    recommendation_candidate_id="cand-1",
                    parameter_values={"resin": 1.0, "hard": 1.0},
                    status=m.ExperimentRunStatus.PENDING,
                    executed_at=None,
                    executed_by=None,
                )
            )
        with pytest.raises(ServiceError):
            service.record_experiment_result(
                "run-1", "exp-x", "user-1", m.ExperimentRunStatus.COMPLETED
            )


class TestRecordMeasurement:
    """Measurements append to the supersede chain and are logged."""

    def test_first_measurement_is_recorded_and_logged(
        self, service, seeded_experiment, make_measurement
    ):
        repo = seeded_experiment
        service.record_measurement(make_measurement(), "user-1")
        readings = repo.list_measurements("exp-1")
        assert [r.value for r in readings] == [76.0]
        logs = repo.list_decision_logs("run-1")
        assert m.DecisionAction.MEASUREMENT_RECORDED in [l.action for l in logs]

    def test_unknown_experiment_rejected(
        self, service, seeded_run, make_measurement
    ):
        with pytest.raises(ServiceError):
            service.record_measurement(
                make_measurement(experiment_run_id="ghost"), "user-1"
            )


class TestCloseRoundReadiness:
    """A round cannot close until the objective's output has a valid reading."""

    def _seed_awaiting(self, repo, factories):
        make_run, make_batch, make_round, make_experiment_run = factories
        with repo.transaction():
            repo.save_run(
                make_run(status=m.RunStatus.AWAITING_MEASUREMENTS)
            )
            repo.add_batch(make_batch())
            repo.add_round(make_round())
            repo.add_experiment_run(
                make_experiment_run(id="exp-1", recommendation_candidate_id="cand-1")
            )
        return repo

    def test_not_ready_cannot_close(
        self, service, seeded_run, make_run, make_batch, make_round,
        make_experiment_run
    ):
        repo = self._seed_awaiting(
            seeded_run, (make_run, make_batch, make_round, make_experiment_run)
        )
        with pytest.raises(ServiceError):
            service.close_round("run-1", "round-1", "user-1")
        assert repo.get_round("round-1").status is m.RoundStatus.OPEN

    def test_ready_closes(
        self, service, seeded_run, make_run, make_batch, make_round,
        make_experiment_run, make_measurement
    ):
        repo = self._seed_awaiting(
            seeded_run, (make_run, make_batch, make_round, make_experiment_run)
        )
        with repo.transaction():
            repo.add_measurement(make_measurement(experiment_run_id="exp-1"))
        run = service.close_round("run-1", "round-1", "user-1")
        assert run.status is m.RunStatus.ROUND_CLOSED
        assert repo.get_round("round-1").status is m.RoundStatus.CLOSED


class TestPersistedImmutability:
    """After creation, a batch's inputs and an experiment's identity are frozen."""

    def test_batch_candidates_are_immutable(
        self, seeded_run, make_batch
    ):
        repo = seeded_run
        with repo.transaction():
            repo.add_batch(make_batch())
        tampered = make_batch(
            candidates=[
                m.RecommendationCandidate(
                    id="cand-9", parameter_values={"resin": 1.0, "hard": 2.0}
                )
            ]
        )
        with repo.transaction():
            with pytest.raises(PersistenceError):
                repo.save_batch(tampered)

    def test_experiment_parameter_values_are_immutable(
        self, seeded_experiment, make_experiment_run
    ):
        repo = seeded_experiment
        tampered = make_experiment_run(parameter_values={"resin": 1.0, "hard": 2.0})
        with repo.transaction():
            with pytest.raises(PersistenceError):
                repo.save_experiment_run(tampered)


class TestSingleRoundHappyPath:
    """The whole loop: validate -> design -> execute -> measure -> close -> done."""

    def test_full_loop(self, service_with_adapter, seeded_run):
        repo = seeded_run
        service = service_with_adapter

        service.validate_design_space("run-1", "user-1")
        assert repo.get_run("run-1").status is m.RunStatus.DESIGN_SPACE_VALIDATED

        batch = service.generate_initial_design("run-1", "user-1")
        experiments = repo.list_experiment_runs_for_run("run-1")
        assert len(experiments) == 4

        for experiment in experiments:
            service.record_experiment_result(
                "run-1", experiment.id, "user-1", m.ExperimentRunStatus.COMPLETED
            )
        assert repo.get_run("run-1").budget_used == 4
        assert repo.get_batch(batch.id).status is m.BatchStatus.FULLY_EXECUTED

        run = service.mark_all_runs_terminal("run-1", "user-1")
        assert run.status is m.RunStatus.AWAITING_MEASUREMENTS

        for experiment in experiments:
            service.record_measurement(
                m.Measurement(
                    id=f"meas-{experiment.id}",
                    experiment_run_id=experiment.id,
                    output_id="o1",
                    value=70.0,
                    status=m.MeasurementStatus.VALID,
                    revision=1,
                    supersedes_measurement_id=None,
                    recorded_at="2026-07-29T02:00:00Z",
                    recorded_by="user-1",
                ),
                "user-1",
            )

        rounds = repo.list_rounds("run-1")
        run = service.close_round("run-1", rounds[0].id, "user-1")
        assert run.status is m.RunStatus.ROUND_CLOSED
        assert repo.get_round(rounds[0].id).status is m.RoundStatus.CLOSED

        run = service.mark_completed("run-1", "user-1")
        assert run.status is m.RunStatus.COMPLETED

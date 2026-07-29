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


class _DuplicateIdAdapter:
    """An adapter that returns two candidates sharing an id (distinct vectors)."""

    def generate_initial_design(self, revision, policy):
        candidates = [
            m.RecommendationCandidate(
                id="cand-1", parameter_values={"resin": 10.0, "hard": 5.0}
            ),
            m.RecommendationCandidate(
                id="cand-1", parameter_values={"resin": 20.0, "hard": 15.0}
            ),
        ]
        candidates += [
            m.RecommendationCandidate(
                id=f"cand-{i}",
                parameter_values={"resin": float(i * 3), "hard": float(i * 7)},
            )
            for i in range(3, policy.batch_size + 1)
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

    def test_unvalidated_run_never_calls_adapter(
        self, service_with_adapter, seeded_run, fake_adapter
    ):
        # A Draft (never-validated) run fails the precondition gate before the
        # adapter is ever called, so nothing is persisted (req 1).
        repo = seeded_run
        with pytest.raises(ServiceError):
            service_with_adapter.generate_initial_design("run-1", "user-1")

        assert fake_adapter.calls == []
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

    def test_batch_size_over_budget_never_calls_adapter(
        self, service_with_adapter, seeded_definition, fake_adapter, make_run
    ):
        # batchSize (4) exceeds the remaining budget (3): the precondition gate
        # fails before the adapter is called and nothing is persisted (req 1, 8).
        repo = seeded_definition
        with repo.transaction():
            repo.add_run(
                make_run(
                    status=m.RunStatus.DESIGN_SPACE_VALIDATED, budget_total=3
                )
            )
        with pytest.raises(ServiceError):
            service_with_adapter.generate_initial_design("run-1", "user-1")
        assert fake_adapter.calls == []
        assert repo.list_batches("run-1") == []
        assert repo.list_rounds("run-1") == []
        assert repo.list_experiment_runs_for_run("run-1") == []

    def test_duplicate_candidate_id_persists_nothing(self, repo, seeded_run):
        # The adapter returns the right count but with a duplicate candidate id;
        # validate_candidates rejects it before any write (req 2, 8).
        service = ApplicationService(repo, adapter=_DuplicateIdAdapter())
        _validated_run(service, repo)
        with pytest.raises(ServiceError):
            service.generate_initial_design("run-1", "user-1")
        assert repo.list_batches("run-1") == []
        assert repo.list_rounds("run-1") == []
        assert repo.list_experiment_runs_for_run("run-1") == []
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

    def test_overwriting_completed_experiment_rejected(
        self, service_with_adapter, seeded_run
    ):
        experiments = self._generate(service_with_adapter, seeded_run)
        service_with_adapter.record_experiment_result(
            "run-1", experiments[0].id, "user-1", m.ExperimentRunStatus.COMPLETED
        )
        with pytest.raises(ServiceError):
            service_with_adapter.record_experiment_result(
                "run-1", experiments[0].id, "user-1", m.ExperimentRunStatus.FAILED
            )

    def test_result_on_completed_run_rejected(
        self, service, seeded_run, make_run, make_batch, make_round,
        make_experiment_run
    ):
        repo = seeded_run
        with repo.transaction():
            repo.save_run(make_run(status=m.RunStatus.COMPLETED, round=1))
            repo.add_batch(make_batch())
            repo.add_round(make_round(status=m.RoundStatus.CLOSED))
            repo.add_experiment_run(
                make_experiment_run(
                    id="exp-1",
                    recommendation_candidate_id="cand-1",
                    status=m.ExperimentRunStatus.PENDING,
                    executed_at=None,
                    executed_by=None,
                )
            )
        with pytest.raises(ServiceError):
            service.record_experiment_result(
                "run-1", "exp-1", "user-1", m.ExperimentRunStatus.COMPLETED
            )

    def test_candidate_value_mismatch_rejected(
        self, service, seeded_run, make_run, make_batch, make_round,
        make_experiment_run
    ):
        repo = seeded_run
        with repo.transaction():
            repo.save_run(make_run(status=m.RunStatus.RECOMMENDATIONS_PENDING))
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
        self, service, seeded_experiment, make_run, make_measurement
    ):
        repo = seeded_experiment
        with repo.transaction():
            repo.save_run(make_run(status=m.RunStatus.AWAITING_MEASUREMENTS))
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

    @pytest.mark.parametrize(
        "exp_status",
        [
            m.ExperimentRunStatus.PENDING,
            m.ExperimentRunStatus.FAILED,
            m.ExperimentRunStatus.CANCELLED,
        ],
    )
    def test_measurement_on_non_completed_experiment_rejected(
        self, service, seeded_run, make_run, make_batch, make_round,
        make_experiment_run, make_measurement, exp_status
    ):
        repo = seeded_run
        executed = exp_status is not m.ExperimentRunStatus.PENDING
        with repo.transaction():
            repo.save_run(make_run(status=m.RunStatus.AWAITING_MEASUREMENTS))
            repo.add_batch(make_batch())
            repo.add_round(make_round())
            repo.add_experiment_run(
                make_experiment_run(
                    id="exp-1",
                    recommendation_candidate_id="cand-1",
                    status=exp_status,
                    executed_at="2026-07-29T01:00:00Z" if executed else None,
                    executed_by="user-1" if executed else None,
                )
            )
        with pytest.raises(ServiceError):
            service.record_measurement(
                make_measurement(experiment_run_id="exp-1"), "user-1"
            )

    def test_recorded_by_must_equal_actor(
        self, service, seeded_experiment, make_run, make_measurement
    ):
        repo = seeded_experiment
        with repo.transaction():
            repo.save_run(make_run(status=m.RunStatus.AWAITING_MEASUREMENTS))
        with pytest.raises(ServiceError):
            service.record_measurement(
                make_measurement(recorded_by="someone-else"), "user-1"
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

    def test_four_completed_only_one_measured_cannot_close(
        self, service_with_adapter, seeded_run
    ):
        # 4 Completed experiments but only 1 has its objective reading: the other
        # 3 are Completed-but-incomplete, so the round cannot close (req 8).
        repo = seeded_run
        service = service_with_adapter
        service.validate_design_space("run-1", "user-1")
        service.generate_initial_design("run-1", "user-1")
        experiments = repo.list_experiment_runs_for_run("run-1")
        for experiment in experiments:
            service.record_experiment_result(
                "run-1", experiment.id, "user-1", m.ExperimentRunStatus.COMPLETED
            )
        service.mark_all_runs_terminal("run-1", "user-1")
        service.record_measurement(
            m.Measurement(
                id="meas-only-one",
                experiment_run_id=experiments[0].id,
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
        with pytest.raises(ServiceError):
            service.close_round("run-1", rounds[0].id, "user-1")
        assert repo.get_round(rounds[0].id).status is m.RoundStatus.OPEN

    def test_multi_objective_scattered_cannot_close(
        self, service, repo, make_definition, make_revision, make_run, make_batch,
        make_round, make_experiment_run, make_measurement
    ):
        # A Desirability objective covers o1 and o2. exp-1 measures only o1 and
        # exp-2 only o2: no single experiment forms a complete row, so readings
        # must not be stitched across experiments and the round cannot close.
        revision = make_revision(
            id="rev-d",
            outputs=[
                m.OutputSpec(id="o1", name="Strength"),
                m.OutputSpec(id="o2", name="Gloss"),
            ],
            targets=[
                m.TargetSpec(id="t1", output_id="o1", direction="Maximize"),
                m.TargetSpec(id="t2", output_id="o2", direction="Maximize"),
            ],
            objective_policy=m.DesirabilityObjectivePolicy(
                entries=[
                    m.DesirabilityEntry(
                        target_id="t1",
                        cutoffs=m.Cutoffs(lower=0, upper=100),
                        weight=1.0,
                    ),
                    m.DesirabilityEntry(
                        target_id="t2",
                        cutoffs=m.Cutoffs(lower=0, upper=100),
                        weight=1.0,
                    ),
                ],
                weighting_mode=m.WeightingMode.EXPLICIT,
            ),
        )
        with repo.transaction():
            repo.add_definition(make_definition(head_revision_id="rev-d"))
            repo.add_revision(revision)
            repo.add_run(
                make_run(
                    definition_revision_id="rev-d",
                    status=m.RunStatus.AWAITING_MEASUREMENTS,
                )
            )
            repo.add_batch(
                make_batch(
                    candidates=[
                        m.RecommendationCandidate(
                            id="cand-1",
                            parameter_values={"resin": 60.0, "hard": 40.0},
                        ),
                        m.RecommendationCandidate(
                            id="cand-2",
                            parameter_values={"resin": 10.0, "hard": 5.0},
                        ),
                    ]
                )
            )
            repo.add_round(make_round())
            repo.add_experiment_run(
                make_experiment_run(id="exp-1", recommendation_candidate_id="cand-1")
            )
            repo.add_experiment_run(
                make_experiment_run(
                    id="exp-2",
                    recommendation_candidate_id="cand-2",
                    parameter_values={"resin": 10.0, "hard": 5.0},
                )
            )
            repo.add_measurement(
                make_measurement(id="m-o1", experiment_run_id="exp-1", output_id="o1")
            )
            repo.add_measurement(
                make_measurement(id="m-o2", experiment_run_id="exp-2", output_id="o2")
            )
        with pytest.raises(ServiceError):
            service.close_round("run-1", "round-1", "user-1")
        assert repo.get_round("round-1").status is m.RoundStatus.OPEN


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

"""Contract tests for the production BayBE adapter's initial-design leg.

These tests run against *real* BayBE (see ``conftest.py``); nothing is mocked.
They pin the parameter/constraint mapping, the recommender selection rules, the
output contract, and the end-to-end atomic persistence through the application
service.
"""

import pytest

from backend.adapters.errors import UnsupportedFeatureError
from backend.application import ApplicationService
from backend.domain import models as m
from backend.domain.validation import validate_candidates

_TOL = 1e-6


# Builders ------------------------------------------------------------------


def _revision(parameters, constraints=()):
    """A valid, confirmed single-objective revision over the given parameters."""
    return m.CampaignDefinitionRevision(
        id="rev-b",
        campaign_definition_id="cd-1",
        revision_number=1,
        parameters=parameters,
        outputs=[m.OutputSpec(id="o1", name="Strength")],
        targets=[m.TargetSpec(id="t1", output_id="o1", direction="Maximize")],
        objective_policy=m.SingleObjectivePolicy(target_id="t1"),
        constraints=list(constraints),
        constraints_confirmed=True,
        constraints_confirmed_at="2026-07-29T00:00:00Z",
        created_at="2026-07-29T00:00:00Z",
        created_by="user-1",
    )


def _two_phase(initial_recommender="RandomRecommender"):
    return m.TwoPhaseMetaConfig(
        initial_recommender=initial_recommender,
        switch_after=5,
        remain_switched=True,
        acquisition_function="qLogEI",
    )


def _policy(strategy, batch_size=3, seed_value=42):
    return m.OptimizationPolicy(
        id="op-b",
        batch_size=batch_size,
        seed_policy=m.SeedPolicy.FIXED,
        seed_value=seed_value,
        strategy_config=strategy,
    )


def _continuous():
    return [
        m.ContinuousParameterSpec(id="resin", name="Resin", bounds=m.Bounds(lower=0, upper=100)),
        m.ContinuousParameterSpec(id="hard", name="Hardener", bounds=m.Bounds(lower=0, upper=100)),
    ]


def _discrete():
    return [
        m.DiscreteParameterSpec(id="a", name="Level A", values=[0, 10, 20, 30]),
        m.DiscreteParameterSpec(id="b", name="Level B", values=[0, 10, 20, 30]),
    ]


class TestParameterMapping:
    """Each parameter type is mapped to the right BayBE parameter by id."""

    def test_continuous_fixed_sum_initial_design(self, baybe_adapter):
        revision = _revision(
            _continuous(),
            [
                m.LinearEqualityConstraintSpec(
                    id="c1", parameter_ids=["resin", "hard"], coefficients=[1, 1], rhs=100
                )
            ],
        )
        result = baybe_adapter.generate_initial_design(revision, _policy(_two_phase(), batch_size=3))

        assert len(result.candidates) == 3
        for candidate in result.candidates:
            values = candidate.parameter_values
            assert set(values) == {"resin", "hard"}
            assert 0 <= values["resin"] <= 100
            assert 0 <= values["hard"] <= 100
            assert abs(values["resin"] + values["hard"] - 100) <= _TOL
        # The domain agrees the candidates are legal for this revision.
        assert validate_candidates(revision, result.candidates).ok is True

    def test_numerical_discrete_parameters(self, baybe_adapter):
        revision = _revision(_discrete())
        result = baybe_adapter.generate_initial_design(revision, _policy(_two_phase(), batch_size=3))

        allowed = {0.0, 10.0, 20.0, 30.0}
        for candidate in result.candidates:
            assert candidate.parameter_values["a"] in allowed
            assert candidate.parameter_values["b"] in allowed
        assert validate_candidates(revision, result.candidates).ok is True

    def test_categorical_parameters(self, baybe_adapter):
        revision = _revision(
            [m.CategoricalParameterSpec(id="cat", name="Catalyst", values=["A", "B", "C"])]
        )
        result = baybe_adapter.generate_initial_design(revision, _policy(_two_phase(), batch_size=3))

        labels = [c.parameter_values["cat"] for c in result.candidates]
        assert all(isinstance(label, str) for label in labels)
        assert set(labels) <= {"A", "B", "C"}
        assert validate_candidates(revision, result.candidates).ok is True


class TestConstraintMapping:
    """Supported constraint shapes are honored by the returned candidates."""

    def test_continuous_linear_inequality_respected(self, baybe_adapter):
        revision = _revision(
            _continuous(),
            [
                m.LinearInequalityConstraintSpec(
                    id="c1",
                    parameter_ids=["resin", "hard"],
                    coefficients=[1, 1],
                    operator="<=",
                    rhs=50,
                )
            ],
        )
        result = baybe_adapter.generate_initial_design(revision, _policy(_two_phase(), batch_size=3))

        for candidate in result.candidates:
            total = candidate.parameter_values["resin"] + candidate.parameter_values["hard"]
            assert total <= 50 + _TOL
        assert validate_candidates(revision, result.candidates).ok is True

    def test_discrete_sum_constraint_respected(self, baybe_adapter):
        revision = _revision(
            _discrete(),
            [
                m.LinearEqualityConstraintSpec(
                    id="c1", parameter_ids=["a", "b"], coefficients=[1, 1], rhs=30
                )
            ],
        )
        result = baybe_adapter.generate_initial_design(revision, _policy(_two_phase(), batch_size=3))

        for candidate in result.candidates:
            total = candidate.parameter_values["a"] + candidate.parameter_values["b"]
            assert abs(total - 30) <= _TOL
        assert validate_candidates(revision, result.candidates).ok is True

    def test_continuous_cardinality_constraint_respected(self, baybe_adapter):
        revision = _revision(
            _continuous(),
            [
                m.CardinalityConstraintSpec(
                    id="c1", parameter_ids=["resin", "hard"], min_cardinality=1, max_cardinality=1
                )
            ],
        )
        result = baybe_adapter.generate_initial_design(revision, _policy(_two_phase(), batch_size=3))

        for candidate in result.candidates:
            active = sum(1 for v in candidate.parameter_values.values() if abs(v) > _TOL)
            assert active == 1
        assert validate_candidates(revision, result.candidates).ok is True

    def test_discrete_cardinality_constraint_respected(self, baybe_adapter):
        revision = _revision(
            _discrete(),
            [
                m.CardinalityConstraintSpec(
                    id="c1", parameter_ids=["a", "b"], min_cardinality=1, max_cardinality=1
                )
            ],
        )
        result = baybe_adapter.generate_initial_design(revision, _policy(_two_phase(), batch_size=3))

        for candidate in result.candidates:
            active = sum(1 for v in candidate.parameter_values.values() if abs(v) > _TOL)
            assert active == 1
        assert validate_candidates(revision, result.candidates).ok is True


class TestUnsupportedInputsFailExplicitly:
    """Unsupported mappings and strategies must raise, never silently degrade."""

    def test_mixed_parameter_linear_constraint_fails(self, baybe_adapter):
        revision = _revision(
            [
                m.ContinuousParameterSpec(id="resin", name="Resin", bounds=m.Bounds(lower=0, upper=100)),
                m.DiscreteParameterSpec(id="lvl", name="Level", values=[0, 10, 20]),
            ],
            [
                m.LinearEqualityConstraintSpec(
                    id="c1", parameter_ids=["resin", "lvl"], coefficients=[1, 1], rhs=50
                )
            ],
        )
        with pytest.raises(UnsupportedFeatureError):
            baybe_adapter.generate_initial_design(revision, _policy(_two_phase()))

    def test_discrete_nonunit_coefficients_fail(self, baybe_adapter):
        revision = _revision(
            _discrete(),
            [
                m.LinearInequalityConstraintSpec(
                    id="c1",
                    parameter_ids=["a", "b"],
                    coefficients=[2, 1],
                    operator="<=",
                    rhs=50,
                )
            ],
        )
        with pytest.raises(UnsupportedFeatureError):
            baybe_adapter.generate_initial_design(revision, _policy(_two_phase()))

    def test_incompatible_fps_config_fails(self, baybe_adapter):
        # FPS needs discrete candidates; a fully continuous space is incompatible.
        revision = _revision(_continuous())
        with pytest.raises(UnsupportedFeatureError):
            baybe_adapter.generate_initial_design(
                revision, _policy(_two_phase("FPSRecommender"))
            )

    def test_direct_botorch_coldstart_fails(self, baybe_adapter):
        # A direct Botorch strategy has no cold-start phase for the initial design.
        revision = _revision(_continuous())
        botorch = m.BotorchConfig(acquisition_function="qLogEI")
        with pytest.raises(UnsupportedFeatureError):
            baybe_adapter.generate_initial_design(revision, _policy(botorch))


class TestOutputContract:
    """The returned candidates obey the adapter's output contract."""

    def test_ids_are_unique_and_no_objectives_are_fabricated(self, baybe_adapter):
        revision = _revision(_continuous())
        result = baybe_adapter.generate_initial_design(revision, _policy(_two_phase(), batch_size=4))

        ids = [c.id for c in result.candidates]
        assert len(ids) == 4
        assert len(set(ids)) == 4
        assert all(ids)
        for candidate in result.candidates:
            assert candidate.predicted_mean is None
            assert candidate.predicted_sd is None
            assert candidate.desirability is None

    def test_values_are_native_python_scalars(self, baybe_adapter):
        revision = _revision(_continuous())
        result = baybe_adapter.generate_initial_design(revision, _policy(_two_phase(), batch_size=2))

        for candidate in result.candidates:
            for value in candidate.parameter_values.values():
                assert type(value) is float

    def test_algorithm_config_records_resolved_run(self, baybe_adapter):
        revision = _revision(_continuous())
        result = baybe_adapter.generate_initial_design(
            revision, _policy(_two_phase(), batch_size=2, seed_value=7)
        )
        config = result.algorithm_config
        assert config.strategy_kind == "TwoPhaseMeta"
        assert config.seed == 7
        assert config.backend_name == "baybe"


class TestApplicationServiceIntegration:
    """The real adapter drives one atomic single-round transaction."""

    def _seed_validated_run(self, repo, make_definition, make_revision, make_run, policy):
        with repo.transaction():
            repo.add_definition(make_definition())
            repo.add_revision(make_revision())
            repo.add_run(
                make_run(
                    status=m.RunStatus.DESIGN_SPACE_VALIDATED,
                    optimization_policy=policy,
                )
            )

    def test_initial_design_persists_batch_round_experiments(
        self, repo, make_definition, make_revision, make_run, baybe_adapter
    ):
        policy = _policy(_two_phase(), batch_size=4, seed_value=7)
        self._seed_validated_run(repo, make_definition, make_revision, make_run, policy)
        service = ApplicationService(repo, adapter=baybe_adapter)

        batch = service.generate_initial_design("run-1", actor="user-1")

        assert len(batch.candidates) == 4
        assert [b.id for b in repo.list_batches("run-1")] == [batch.id]
        rounds = repo.list_rounds("run-1")
        assert len(rounds) == 1
        assert rounds[0].status is m.RoundStatus.OPEN
        experiments = repo.list_experiment_runs(rounds[0].id)
        assert len(experiments) == 4
        assert all(e.status is m.ExperimentRunStatus.PENDING for e in experiments)
        assert repo.get_run("run-1").status is m.RunStatus.RECOMMENDATIONS_PENDING

    def test_adapter_failure_leaves_no_batch_round_or_experiment(
        self, repo, make_definition, make_revision, make_run, baybe_adapter
    ):
        # A direct Botorch policy makes the adapter reject the initial design;
        # the whole transaction must roll back with nothing persisted.
        policy = _policy(m.BotorchConfig(acquisition_function="qLogEI"), batch_size=4)
        self._seed_validated_run(repo, make_definition, make_revision, make_run, policy)
        service = ApplicationService(repo, adapter=baybe_adapter)

        with pytest.raises(UnsupportedFeatureError):
            service.generate_initial_design("run-1", actor="user-1")

        assert repo.list_batches("run-1") == []
        assert repo.list_rounds("run-1") == []
        assert repo.list_experiment_runs_for_run("run-1") == []
        assert repo.get_run("run-1").status is m.RunStatus.DESIGN_SPACE_VALIDATED

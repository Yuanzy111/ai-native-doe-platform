"""Tests for the domain models and their discriminated unions (§2)."""

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.domain import models as m


class TestDiscriminatedUnions:
    """Each union routes on its discriminant and round-trips through JSON."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (
                {"type": "Continuous", "id": "p", "name": "n", "bounds": {"lower": 0, "upper": 1}},
                m.ContinuousParameterSpec,
            ),
            (
                {"type": "Discrete", "id": "p", "name": "n", "values": [1, 2, 3]},
                m.DiscreteParameterSpec,
            ),
            (
                {"type": "Categorical", "id": "p", "name": "n", "values": ["a", "b"]},
                m.CategoricalParameterSpec,
            ),
        ],
    )
    def test_parameter_spec_routes_on_type(self, payload, expected):
        adapter = TypeAdapter(m.ParameterSpec)
        assert isinstance(adapter.validate_python(payload), expected)

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"kind": "Single", "targetId": "t"}, m.SingleObjectivePolicy),
            (
                {
                    "kind": "Desirability",
                    "entries": [
                        {"targetId": "t", "cutoffs": {"lower": 0, "upper": 1}, "weight": 1.0}
                    ],
                    "weightingMode": "equal",
                },
                m.DesirabilityObjectivePolicy,
            ),
            ({"kind": "Pareto", "targetIds": ["a", "b"]}, m.ParetoObjectivePolicy),
        ],
    )
    def test_objective_policy_routes_on_kind(self, payload, expected):
        adapter = TypeAdapter(m.ObjectivePolicy)
        assert isinstance(adapter.validate_python(payload), expected)

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (
                {"kind": "LinearEquality", "id": "c", "parameterIds": ["a", "b"], "coefficients": [1, 1], "rhs": 100},
                m.LinearEqualityConstraintSpec,
            ),
            (
                {"kind": "LinearInequality", "id": "c", "parameterIds": ["a", "b"], "coefficients": [1, 1], "operator": "<=", "rhs": 100},
                m.LinearInequalityConstraintSpec,
            ),
            (
                {"kind": "Cardinality", "id": "c", "parameterIds": ["a", "b"], "minCardinality": 1, "maxCardinality": 2},
                m.CardinalityConstraintSpec,
            ),
        ],
    )
    def test_constraint_spec_routes_on_kind(self, payload, expected):
        adapter = TypeAdapter(m.ConstraintSpec)
        assert isinstance(adapter.validate_python(payload), expected)

    def test_revision_round_trips_through_camel_case_json(self, make_revision):
        revision = make_revision(
            constraints=[
                m.LinearEqualityConstraintSpec(
                    id="c1", parameter_ids=["resin", "hard"], coefficients=[1, 1], rhs=100
                )
            ]
        )
        dumped = revision.model_dump(by_alias=True, mode="json")
        assert "revisionNumber" in dumped
        assert dumped["constraints"][0]["kind"] == "LinearEquality"

        reloaded = m.CampaignDefinitionRevision.model_validate(dumped)
        assert reloaded == revision


class TestNormalization:
    """Value fields normalize as documented in §2.4."""

    def test_discrete_values_are_deduped_and_sorted(self):
        spec = m.DiscreteParameterSpec(id="p", name="n", values=[50, 30, 30, 40])
        assert spec.values == [30.0, 40.0, 50.0]

    def test_categorical_values_dedup_preserving_order(self):
        spec = m.CategoricalParameterSpec(id="p", name="n", values=["B", "A", "B"])
        assert spec.values == ["B", "A"]

    def test_blank_categorical_value_is_rejected(self):
        with pytest.raises(ValidationError):
            m.CategoricalParameterSpec(id="p", name="n", values=["A", "  "])


class TestIntrinsicValidators:
    """Per-instance structural invariants raise at construction."""

    def test_continuous_bounds_must_increase(self):
        with pytest.raises(ValidationError):
            m.ContinuousParameterSpec(id="p", name="n", bounds=m.Bounds(lower=5, upper=5))

    def test_linear_constraint_coefficient_length_must_match(self):
        with pytest.raises(ValidationError):
            m.LinearEqualityConstraintSpec(
                id="c", parameter_ids=["a", "b"], coefficients=[1], rhs=1
            )

    def test_cardinality_bounds_must_be_ordered(self):
        with pytest.raises(ValidationError):
            m.CardinalityConstraintSpec(
                id="c", parameter_ids=["a", "b"], min_cardinality=2, max_cardinality=1
            )

    def test_cardinality_max_cannot_exceed_parameter_count(self):
        with pytest.raises(ValidationError):
            m.CardinalityConstraintSpec(
                id="c", parameter_ids=["a", "b"], min_cardinality=0, max_cardinality=3
            )

    def test_fixed_seed_requires_value(self):
        with pytest.raises(ValidationError):
            m.OptimizationPolicy(
                id="op",
                batch_size=1,
                seed_policy=m.SeedPolicy.FIXED,
                strategy_config=m.BotorchConfig(acquisition_function="qLogEI"),
            )

    def test_auto_seed_does_not_require_value(self):
        policy = m.OptimizationPolicy(
            id="op",
            batch_size=1,
            seed_policy=m.SeedPolicy.AUTO_GENERATED,
            strategy_config=m.BotorchConfig(acquisition_function="qLogEI"),
        )
        assert policy.seed_value is None

    @pytest.mark.parametrize("status", [m.ExperimentRunStatus.COMPLETED, m.ExperimentRunStatus.FAILED])
    def test_terminal_experiment_run_requires_execution_metadata(self, status):
        with pytest.raises(ValidationError):
            m.ExperimentRun(
                id="e",
                campaign_run_id="r",
                experiment_round_id="rd",
                parameter_values={"p": 1.0},
                status=status,
            )

    def test_duplicate_parameter_ids_in_revision_are_rejected(self, make_revision):
        with pytest.raises(ValidationError):
            make_revision(
                parameters=[
                    m.ContinuousParameterSpec(id="dup", name="a", bounds=m.Bounds(lower=0, upper=1)),
                    m.ContinuousParameterSpec(id="dup", name="b", bounds=m.Bounds(lower=0, upper=1)),
                ]
            )

    def test_confirmed_constraints_require_timestamp(self, make_revision):
        with pytest.raises(ValidationError):
            make_revision(constraints_confirmed=True, constraints_confirmed_at=None)


class TestSerializationTags:
    """Enums serialize to their documented string values."""

    def test_run_status_serializes_to_string(self, make_run):
        run = make_run(status=m.RunStatus.AWAITING_MEASUREMENTS)
        assert run.model_dump(by_alias=True, mode="json")["status"] == "AwaitingMeasurements"

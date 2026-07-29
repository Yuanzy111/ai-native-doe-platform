"""Counterexamples for the model/validation hardening pass (§2, §4).

Each test pins one invariant that the hardening added: immutable nested
collections, ``model_copy`` re-validation, timezone-aware timestamps, NaN/Inf/
bool rejection, whitespace-stripped identifiers, and the tightened objective /
constraint checks in ``validate_definition`` / ``validate_candidates``.
"""

import pytest
from pydantic import ValidationError

from backend.domain import models as m
from backend.domain.validation import validate_candidates, validate_definition


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


class TestNestedImmutability:
    """Frozen value objects must reject in-place mutation of their collections."""

    def test_parameter_list_cannot_be_appended(self, make_revision):
        revision = make_revision()
        with pytest.raises(TypeError):
            revision.parameters.append(revision.parameters[0])

    def test_parameter_list_cannot_be_reassigned_by_index(self, make_revision):
        revision = make_revision()
        with pytest.raises(TypeError):
            revision.parameters[0] = revision.parameters[1]

    def test_discrete_values_are_frozen(self):
        spec = m.DiscreteParameterSpec(id="d", name="Level", values=[10, 20, 30])
        with pytest.raises(TypeError):
            spec.values.append(40)

    def test_candidate_value_map_is_frozen(self):
        candidate = m.RecommendationCandidate(
            id="c1", parameter_values={"resin": 60.0, "hard": 40.0}
        )
        with pytest.raises(TypeError):
            candidate.parameter_values["resin"] = 0.0

    def test_setting_a_frozen_field_is_rejected(self, make_revision):
        revision = make_revision()
        with pytest.raises(ValidationError):
            revision.revision_number = 2


class TestModelCopyCannotBypassValidation:
    """model_copy(update=...) must re-run validators, not blindly assign."""

    def test_budget_invariant_survives_model_copy(self, make_run):
        run = make_run(budget_total=10, budget_used=0)
        with pytest.raises(ValidationError):
            run.model_copy(update={"budget_used": 999})

    def test_model_copy_without_update_still_copies(self, make_run):
        run = make_run()
        assert run.model_copy() == run


class TestNumericHardening:
    """Numeric fields reject bool, NaN, and Inf."""

    def test_bool_is_not_a_number(self):
        with pytest.raises(ValidationError):
            m.Bounds(lower=True, upper=1)

    def test_nan_is_rejected(self):
        with pytest.raises(ValidationError):
            m.Bounds(lower=float("nan"), upper=1)

    def test_inf_is_rejected(self):
        with pytest.raises(ValidationError):
            m.Bounds(lower=0, upper=float("inf"))


class TestTimestampHardening:
    """Timestamps must be timezone-aware."""

    def test_naive_datetime_is_rejected(self, make_run):
        with pytest.raises(ValidationError):
            make_run(created_at="2026-07-29T00:00:00")

    def test_garbage_datetime_is_rejected(self, make_run):
        with pytest.raises(ValidationError):
            make_run(created_at="not-a-date")


class TestIdentifierHardening:
    """Identifiers and names are stripped and must be non-blank."""

    def test_blank_id_is_rejected(self):
        with pytest.raises(ValidationError):
            m.OutputSpec(id="   ", name="A")

    def test_surrounding_whitespace_is_stripped(self):
        assert m.OutputSpec(id="  o1  ", name="A").id == "o1"


class TestTightenedValidation:
    """The §4 checks that the hardening added or tightened."""

    def test_categorical_parameter_in_linear_constraint_blocks(self, make_revision):
        revision = make_revision(
            parameters=[
                m.ContinuousParameterSpec(
                    id="resin", name="Resin", bounds=m.Bounds(lower=0, upper=100)
                ),
                m.CategoricalParameterSpec(id="cat", name="Catalyst", values=["A", "B"]),
            ],
            constraints=[
                m.LinearEqualityConstraintSpec(
                    id="c1", parameter_ids=["resin", "cat"], coefficients=[1, 1], rhs=100
                )
            ],
        )
        assert "CONSTRAINT_NON_NUMERIC_PARAMETER" in _codes(validate_definition(revision))

    def test_undecidable_candidate_constraint_is_blocking(self, make_revision):
        revision = make_revision(
            parameters=[
                m.ContinuousParameterSpec(
                    id="resin", name="Resin", bounds=m.Bounds(lower=0, upper=100)
                ),
                m.CategoricalParameterSpec(id="cat", name="Catalyst", values=["A", "B"]),
            ],
            constraints=[
                m.LinearEqualityConstraintSpec(
                    id="c1", parameter_ids=["resin", "cat"], coefficients=[1, 1], rhs=100
                )
            ],
        )
        candidate = m.RecommendationCandidate(
            id="cand-1", parameter_values={"resin": 60.0, "cat": "A"}
        )
        result = validate_candidates(revision, [candidate])
        assert "CANDIDATE_CONSTRAINT_UNDECIDABLE" in _codes(result)

    def test_pareto_must_cover_every_target(self, make_revision):
        revision = make_revision(
            outputs=[
                m.OutputSpec(id="o1", name="A"),
                m.OutputSpec(id="o2", name="B"),
                m.OutputSpec(id="o3", name="C"),
            ],
            targets=[
                m.TargetSpec(id="t1", output_id="o1", direction="Maximize"),
                m.TargetSpec(id="t2", output_id="o2", direction="Minimize"),
                m.TargetSpec(id="t3", output_id="o3", direction="Maximize"),
            ],
            objective_policy=m.ParetoObjectivePolicy(target_ids=["t1", "t2"]),
        )
        assert "PARETO_COVERAGE" in _codes(validate_definition(revision))

    def test_desirability_equal_mode_uses_tolerance(self, make_revision):
        revision = make_revision(
            outputs=[m.OutputSpec(id="o1", name="A"), m.OutputSpec(id="o2", name="B")],
            targets=[
                m.TargetSpec(id="t1", output_id="o1", direction="Maximize"),
                m.TargetSpec(id="t2", output_id="o2", direction="Minimize"),
            ],
            objective_policy=m.DesirabilityObjectivePolicy(
                entries=[
                    m.DesirabilityEntry(
                        target_id="t1", cutoffs=m.Cutoffs(lower=0, upper=1), weight=1.0
                    ),
                    m.DesirabilityEntry(
                        target_id="t2",
                        cutoffs=m.Cutoffs(lower=0, upper=1),
                        weight=1.0 + 1e-12,
                    ),
                ],
                weighting_mode="equal",
            ),
        )
        assert validate_definition(revision).ok is True

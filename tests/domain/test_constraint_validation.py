"""Tests for backend-agnostic definition and candidate validation (§4, §4.1)."""

import pytest

from backend.domain import models as m
from backend.domain.validation import validate_candidates, validate_definition


def _codes(result) -> set[str]:
    """Return the set of issue codes in a validation result."""
    return {issue.code for issue in result.issues}


class TestValidateDefinition:
    """validate_definition reports semantic/referential blocking issues."""

    def test_confirmed_single_objective_is_ok(self, make_revision):
        assert validate_definition(make_revision()).ok is True

    def test_unconfirmed_constraints_block(self, make_revision):
        result = validate_definition(
            make_revision(constraints_confirmed=False, constraints_confirmed_at=None)
        )
        assert "CONSTRAINTS_NOT_CONFIRMED" in _codes(result)

    def test_constraint_referencing_unknown_parameter_blocks(self, make_revision):
        revision = make_revision(
            constraints=[
                m.LinearEqualityConstraintSpec(
                    id="c1", parameter_ids=["resin", "ghost"], coefficients=[1, 1], rhs=100
                )
            ]
        )
        assert "CONSTRAINT_UNKNOWN_PARAMETER" in _codes(validate_definition(revision))

    def test_empty_cardinality_constraint_blocks(self, make_revision):
        revision = make_revision(
            constraints=[
                m.CardinalityConstraintSpec(
                    id="c1", parameter_ids=["resin", "hard"], min_cardinality=0, max_cardinality=2
                )
            ]
        )
        assert "CARDINALITY_EMPTY" in _codes(validate_definition(revision))

    def test_duplicate_parameter_name_blocks(self, make_revision):
        revision = make_revision(
            parameters=[
                m.ContinuousParameterSpec(id="a", name="Temp", bounds=m.Bounds(lower=0, upper=1)),
                m.ContinuousParameterSpec(id="b", name="temp", bounds=m.Bounds(lower=0, upper=1)),
            ]
        )
        assert "DUPLICATE_PARAMETER_NAME" in _codes(validate_definition(revision))

    def test_output_without_target_blocks(self, make_revision):
        revision = make_revision(
            outputs=[m.OutputSpec(id="o1", name="A"), m.OutputSpec(id="o2", name="B")],
        )
        assert "OUTPUT_TARGET_CARDINALITY" in _codes(validate_definition(revision))

    def test_single_objective_with_multiple_targets_blocks(self, make_revision):
        revision = make_revision(
            outputs=[m.OutputSpec(id="o1", name="A"), m.OutputSpec(id="o2", name="B")],
            targets=[
                m.TargetSpec(id="t1", output_id="o1", direction="Maximize"),
                m.TargetSpec(id="t2", output_id="o2", direction="Minimize"),
            ],
            objective_policy=m.SingleObjectivePolicy(target_id="t1"),
        )
        assert "OBJECTIVE_TARGET_COUNT" in _codes(validate_definition(revision))

    def _desirability_revision(self, make_revision, cutoffs, weights, mode="explicit"):
        return make_revision(
            outputs=[m.OutputSpec(id="o1", name="A"), m.OutputSpec(id="o2", name="B")],
            targets=[
                m.TargetSpec(id="t1", output_id="o1", direction="Maximize"),
                m.TargetSpec(id="t2", output_id="o2", direction="Minimize"),
            ],
            objective_policy=m.DesirabilityObjectivePolicy(
                entries=[
                    m.DesirabilityEntry(target_id="t1", cutoffs=m.Cutoffs(**cutoffs[0]), weight=weights[0]),
                    m.DesirabilityEntry(target_id="t2", cutoffs=m.Cutoffs(**cutoffs[1]), weight=weights[1]),
                ],
                weighting_mode=mode,
            ),
        )

    def test_valid_desirability_is_ok(self, make_revision):
        revision = self._desirability_revision(
            make_revision,
            cutoffs=[{"lower": 0, "upper": 100}, {"lower": 0, "upper": 10}],
            weights=[1.0, 1.0],
            mode="equal",
        )
        assert validate_definition(revision).ok is True

    def test_desirability_illegal_cutoffs_block(self, make_revision):
        revision = self._desirability_revision(
            make_revision,
            cutoffs=[{"lower": 100, "upper": 100}, {"lower": 0, "upper": 10}],
            weights=[1.0, 1.0],
        )
        assert "DESIRABILITY_CUTOFFS_INVALID" in _codes(validate_definition(revision))

    def test_desirability_equal_mode_requires_equal_weights(self, make_revision):
        revision = self._desirability_revision(
            make_revision,
            cutoffs=[{"lower": 0, "upper": 100}, {"lower": 0, "upper": 10}],
            weights=[1.0, 2.0],
            mode="equal",
        )
        assert "DESIRABILITY_WEIGHTS_NOT_EQUAL" in _codes(validate_definition(revision))

    def test_desirability_missing_coverage_blocks(self, make_revision):
        revision = make_revision(
            outputs=[m.OutputSpec(id="o1", name="A"), m.OutputSpec(id="o2", name="B")],
            targets=[
                m.TargetSpec(id="t1", output_id="o1", direction="Maximize"),
                m.TargetSpec(id="t2", output_id="o2", direction="Minimize"),
            ],
            objective_policy=m.DesirabilityObjectivePolicy(
                entries=[
                    m.DesirabilityEntry(target_id="t1", cutoffs=m.Cutoffs(lower=0, upper=1), weight=1.0)
                ],
                weighting_mode="explicit",
            ),
        )
        assert "DESIRABILITY_COVERAGE" in _codes(validate_definition(revision))


class TestValidateCandidates:
    """validate_candidates enforces the §4.1 post-candidate result checks."""

    @pytest.fixture
    def fixed_sum_revision(self, make_revision):
        return make_revision(
            constraints=[
                m.LinearEqualityConstraintSpec(
                    id="c1", parameter_ids=["resin", "hard"], coefficients=[1, 1], rhs=100
                )
            ]
        )

    def test_valid_candidate_passes(self, fixed_sum_revision):
        candidate = m.RecommendationCandidate(
            id="cand-1", parameter_values={"resin": 60.0, "hard": 40.0}
        )
        assert validate_candidates(fixed_sum_revision, [candidate]).ok is True

    def test_missing_key_is_blocking(self, fixed_sum_revision):
        candidate = m.RecommendationCandidate(id="cand-1", parameter_values={"resin": 60.0})
        assert "CANDIDATE_KEY_MISMATCH" in _codes(validate_candidates(fixed_sum_revision, [candidate]))

    def test_extra_key_is_blocking(self, fixed_sum_revision):
        candidate = m.RecommendationCandidate(
            id="cand-1", parameter_values={"resin": 60.0, "hard": 40.0, "extra": 1.0}
        )
        assert "CANDIDATE_KEY_MISMATCH" in _codes(validate_candidates(fixed_sum_revision, [candidate]))

    def test_out_of_bounds_is_blocking(self, fixed_sum_revision):
        candidate = m.RecommendationCandidate(
            id="cand-1", parameter_values={"resin": 120.0, "hard": -20.0}
        )
        assert "CANDIDATE_OUT_OF_BOUNDS" in _codes(validate_candidates(fixed_sum_revision, [candidate]))

    def test_constraint_violation_is_blocking(self, fixed_sum_revision):
        candidate = m.RecommendationCandidate(
            id="cand-1", parameter_values={"resin": 60.0, "hard": 50.0}
        )
        assert "CANDIDATE_CONSTRAINT_VIOLATED" in _codes(validate_candidates(fixed_sum_revision, [candidate]))

    def test_in_batch_duplicate_is_blocking(self, fixed_sum_revision):
        candidates = [
            m.RecommendationCandidate(id="cand-1", parameter_values={"resin": 60.0, "hard": 40.0}),
            m.RecommendationCandidate(id="cand-2", parameter_values={"resin": 60.0, "hard": 40.0}),
        ]
        assert "CANDIDATE_DUPLICATE" in _codes(validate_candidates(fixed_sum_revision, candidates))

    def test_type_mismatch_is_blocking(self, make_revision):
        revision = make_revision(
            parameters=[m.CategoricalParameterSpec(id="cat", name="Catalyst", values=["A", "B"])],
        )
        candidate = m.RecommendationCandidate(id="cand-1", parameter_values={"cat": 3.0})
        assert "CANDIDATE_TYPE_MISMATCH" in _codes(validate_candidates(revision, [candidate]))

    def test_disallowed_discrete_value_is_blocking(self, make_revision):
        revision = make_revision(
            parameters=[m.DiscreteParameterSpec(id="d", name="Level", values=[10, 20, 30])],
        )
        candidate = m.RecommendationCandidate(id="cand-1", parameter_values={"d": 25.0})
        assert "CANDIDATE_NOT_ALLOWED" in _codes(validate_candidates(revision, [candidate]))

    def test_stepsize_misalignment_is_blocking(self, make_revision):
        revision = make_revision(
            parameters=[
                m.ContinuousParameterSpec(
                    id="c", name="Temp", bounds=m.Bounds(lower=0, upper=100, stepsize=10)
                )
            ],
        )
        candidate = m.RecommendationCandidate(id="cand-1", parameter_values={"c": 15.0})
        assert "CANDIDATE_STEP_MISALIGNED" in _codes(validate_candidates(revision, [candidate]))

    @pytest.mark.parametrize(
        ("operator", "resin", "expected_ok"),
        [("<=", 30.0, True), ("<=", 90.0, False), (">=", 90.0, True), (">=", 30.0, False)],
    )
    def test_linear_inequality_is_evaluated(self, make_revision, operator, resin, expected_ok):
        revision = make_revision(
            constraints=[
                m.LinearInequalityConstraintSpec(
                    id="c1",
                    parameter_ids=["resin", "hard"],
                    coefficients=[1, 0],
                    operator=operator,
                    rhs=50,
                )
            ]
        )
        candidate = m.RecommendationCandidate(
            id="cand-1", parameter_values={"resin": resin, "hard": 0.0}
        )
        assert validate_candidates(revision, [candidate]).ok is expected_ok

    def test_cardinality_is_evaluated(self, make_revision):
        revision = make_revision(
            constraints=[
                m.CardinalityConstraintSpec(
                    id="c1", parameter_ids=["resin", "hard"], min_cardinality=1, max_cardinality=1
                )
            ]
        )
        both_active = m.RecommendationCandidate(
            id="cand-1", parameter_values={"resin": 10.0, "hard": 10.0}
        )
        one_active = m.RecommendationCandidate(
            id="cand-2", parameter_values={"resin": 10.0, "hard": 0.0}
        )
        assert not validate_candidates(revision, [both_active]).ok
        assert validate_candidates(revision, [one_active]).ok

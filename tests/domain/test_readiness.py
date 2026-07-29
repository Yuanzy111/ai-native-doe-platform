"""Unit tests for the per-experiment readiness assessment (§3.5, req 5-7)."""

from backend.domain import models as m
from backend.domain.validation import ReadinessResult, assess_readiness


def _desirability_revision(make_revision):
    """A two-output Desirability revision covering o1 and o2."""
    return make_revision(
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
                    target_id="t1", cutoffs=m.Cutoffs(lower=0, upper=100), weight=1.0
                ),
                m.DesirabilityEntry(
                    target_id="t2", cutoffs=m.Cutoffs(lower=0, upper=100), weight=1.0
                ),
            ],
            weighting_mode=m.WeightingMode.EXPLICIT,
        ),
    )


class TestAssessReadiness:
    """Readiness rows are assembled per experiment; no cross-experiment stitching."""

    def test_single_objective_complete_is_ready(
        self, make_revision, make_experiment_run, make_measurement
    ):
        revision = make_revision()
        experiments = [make_experiment_run(id="exp-1", recommendation_candidate_id="c1")]
        measurements = [make_measurement(id="m1", experiment_run_id="exp-1", output_id="o1")]
        result = assess_readiness(revision, experiments, measurements)
        assert isinstance(result, ReadinessResult)
        assert result.ready is True
        assert result.usable_experiment_run_ids == ("exp-1",)
        assert result.incomplete_experiment_run_ids == ()

    def test_completed_without_reading_is_incomplete(
        self, make_revision, make_experiment_run
    ):
        revision = make_revision()
        experiments = [make_experiment_run(id="exp-1", recommendation_candidate_id="c1")]
        result = assess_readiness(revision, experiments, [])
        assert result.ready is False
        assert result.incomplete_experiment_run_ids == ("exp-1",)
        assert result.usable_experiment_run_ids == ()
        assert "INCOMPLETE_EXPERIMENT_RESULT" in {i.code for i in result.issues}

    def test_no_completed_experiment_has_no_usable_row(
        self, make_revision, make_experiment_run
    ):
        revision = make_revision()
        experiments = [
            make_experiment_run(
                id="exp-1",
                recommendation_candidate_id="c1",
                status=m.ExperimentRunStatus.FAILED,
                executed_at="2026-07-29T01:00:00Z",
                executed_by="user-1",
            )
        ]
        result = assess_readiness(revision, experiments, [])
        assert result.ready is False
        assert result.usable_experiment_run_ids == ()
        assert result.incomplete_experiment_run_ids == ()
        assert "NO_USABLE_DATA_ROW" in {i.code for i in result.issues}

    def test_scattered_multi_objective_is_not_ready(
        self, make_revision, make_experiment_run, make_measurement
    ):
        revision = _desirability_revision(make_revision)
        experiments = [
            make_experiment_run(id="exp-1", recommendation_candidate_id="c1"),
            make_experiment_run(
                id="exp-2",
                recommendation_candidate_id="c2",
                parameter_values={"resin": 10.0, "hard": 5.0},
            ),
        ]
        measurements = [
            make_measurement(id="m1", experiment_run_id="exp-1", output_id="o1"),
            make_measurement(id="m2", experiment_run_id="exp-2", output_id="o2"),
        ]
        result = assess_readiness(revision, experiments, measurements)
        assert result.ready is False
        assert set(result.incomplete_experiment_run_ids) == {"exp-1", "exp-2"}
        assert result.usable_experiment_run_ids == ()

    def test_one_complete_row_amid_incomplete_still_blocks(
        self, make_revision, make_experiment_run, make_measurement
    ):
        revision = _desirability_revision(make_revision)
        experiments = [
            make_experiment_run(id="exp-1", recommendation_candidate_id="c1"),
            make_experiment_run(
                id="exp-2",
                recommendation_candidate_id="c2",
                parameter_values={"resin": 10.0, "hard": 5.0},
            ),
        ]
        measurements = [
            make_measurement(id="m1", experiment_run_id="exp-1", output_id="o1"),
            make_measurement(id="m2", experiment_run_id="exp-1", output_id="o2"),
            make_measurement(id="m3", experiment_run_id="exp-2", output_id="o1"),
        ]
        result = assess_readiness(revision, experiments, measurements)
        assert result.usable_experiment_run_ids == ("exp-1",)
        assert result.incomplete_experiment_run_ids == ("exp-2",)
        # A usable row exists, but a Completed-but-incomplete experiment blocks.
        assert result.ready is False

    def test_superseded_reading_does_not_count(
        self, make_revision, make_experiment_run, make_measurement
    ):
        revision = make_revision()
        experiments = [make_experiment_run(id="exp-1", recommendation_candidate_id="c1")]
        measurements = [
            make_measurement(
                id="m1",
                experiment_run_id="exp-1",
                output_id="o1",
                status=m.MeasurementStatus.INVALID,
            ),
            make_measurement(
                id="m2",
                experiment_run_id="exp-1",
                output_id="o1",
                revision=2,
                supersedes_measurement_id="m1",
            ),
        ]
        # m2 is the active valid head, so the row is complete and ready.
        result = assess_readiness(revision, experiments, measurements)
        assert result.ready is True
        assert result.usable_experiment_run_ids == ("exp-1",)

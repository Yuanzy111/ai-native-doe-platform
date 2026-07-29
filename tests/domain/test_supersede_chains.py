"""Adversarial counterexamples for ``validate_supersede_chains`` (§2.12).

Each ``(experimentRunId, outputId)`` must form a single linear supersede chain:
globally unique ids; per-key revisions ``1..n`` with no gaps or duplicates; every
revision after the first superseding exactly the immediately preceding one; no
branch, no cycle, and exactly one head. These tests pin down each violation.
"""

from backend.domain import models as m
from backend.domain.validation import validate_supersede_chains


def _measurement(**overrides) -> m.Measurement:
    """Build a valid first-revision measurement, overriding selected fields."""
    defaults = dict(
        id="m1",
        experiment_run_id="e1",
        output_id="o1",
        value=10.0,
        status=m.MeasurementStatus.VALID,
        revision=1,
        supersedes_measurement_id=None,
        recorded_at="2026-07-30T00:00:00Z",
        recorded_by="user-1",
    )
    defaults.update(overrides)
    return m.Measurement(**defaults)


def _codes(measurements: list[m.Measurement]) -> set[str]:
    """Return the set of issue codes raised for the given measurements."""
    return {issue.code for issue in validate_supersede_chains(measurements).issues}


def test_valid_chain_has_no_issues():
    m1 = _measurement(id="m1", revision=1)
    m2 = _measurement(id="m2", revision=2, supersedes_measurement_id="m1")
    m3 = _measurement(id="m3", revision=3, supersedes_measurement_id="m2")
    assert validate_supersede_chains([m1, m2, m3]).ok is True


def test_duplicate_measurement_id_is_blocking():
    a = _measurement(id="dup", experiment_run_id="e1", revision=1)
    b = _measurement(id="dup", experiment_run_id="e2", revision=1)
    assert "DUPLICATE_MEASUREMENT_ID" in _codes([a, b])


def test_duplicate_revision_is_blocking():
    m1 = _measurement(id="m1", revision=1)
    m2 = _measurement(id="m2", revision=2, supersedes_measurement_id="m1")
    m3 = _measurement(id="m3", revision=2, supersedes_measurement_id="m1")
    assert "DUPLICATE_REVISION" in _codes([m1, m2, m3])


def test_revision_not_contiguous_is_blocking():
    m1 = _measurement(id="m1", revision=1)
    m3 = _measurement(id="m3", revision=3, supersedes_measurement_id="m1")
    assert "REVISION_NOT_CONTIGUOUS" in _codes([m1, m3])


def test_supersedes_not_predecessor_is_blocking():
    m1 = _measurement(id="m1", revision=1)
    m2 = _measurement(id="m2", revision=2, supersedes_measurement_id="m1")
    # revision 3 must supersede revision 2 (m2), not m1.
    m3 = _measurement(id="m3", revision=3, supersedes_measurement_id="m1")
    assert "SUPERSEDES_NOT_PREDECESSOR" in _codes([m1, m2, m3])


def test_supersede_branch_is_blocking():
    m1 = _measurement(id="m1", revision=1)
    m2 = _measurement(id="m2", revision=2, supersedes_measurement_id="m1")
    m3 = _measurement(id="m3", revision=3, supersedes_measurement_id="m1")
    assert "SUPERSEDE_BRANCH" in _codes([m1, m2, m3])


def test_supersede_cycle_is_blocking():
    # Two readings on one key that supersede each other form a cycle.
    a = _measurement(id="a", revision=2, supersedes_measurement_id="b")
    b = _measurement(id="b", revision=2, supersedes_measurement_id="a")
    assert "SUPERSEDE_CYCLE" in _codes([a, b])

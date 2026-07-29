"""Tests for versioned measurements and supersede chains (§2.12)."""

import pytest
from pydantic import ValidationError

from backend.domain import models as m
from backend.domain.validation import active_measurements, validate_supersede_chains


def _measurement(**overrides) -> m.Measurement:
    """Build a valid measurement, overriding selected fields."""
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


class TestModelInvariant:
    """The revision number and supersede pointer must agree."""

    def test_first_revision_must_not_supersede(self):
        with pytest.raises(ValidationError):
            _measurement(revision=1, supersedes_measurement_id="m0")

    def test_later_revision_must_supersede(self):
        with pytest.raises(ValidationError):
            _measurement(revision=2, supersedes_measurement_id=None)


class TestActiveReading:
    """The active reading is the valid head of the supersede chain."""

    def test_head_of_chain_is_active(self):
        m1 = _measurement(id="m1", revision=1, value=76.0)
        m2 = _measurement(id="m2", revision=2, supersedes_measurement_id="m1", value=78.4)
        active = active_measurements([m1, m2])
        assert [(a.id, a.value) for a in active] == [("m2", 78.4)]

    def test_invalid_head_yields_no_active_reading(self):
        m1 = _measurement(id="m1", revision=1, value=76.0)
        m2 = _measurement(
            id="m2",
            revision=2,
            supersedes_measurement_id="m1",
            status=m.MeasurementStatus.INVALID,
            value=999.0,
        )
        assert active_measurements([m1, m2]) == []

    def test_independent_keys_each_keep_their_head(self):
        a = _measurement(id="a", experiment_run_id="e1", output_id="o1")
        b = _measurement(id="b", experiment_run_id="e2", output_id="o1")
        active_ids = {x.id for x in active_measurements([a, b])}
        assert active_ids == {"a", "b"}


class TestChainIntegrity:
    """validate_supersede_chains flags malformed chains."""

    def test_valid_chain_has_no_issues(self):
        m1 = _measurement(id="m1", revision=1)
        m2 = _measurement(id="m2", revision=2, supersedes_measurement_id="m1")
        assert validate_supersede_chains([m1, m2]).ok is True

    def test_dangling_supersede_pointer_is_blocking(self):
        orphan = _measurement(id="m2", revision=2, supersedes_measurement_id="ghost")
        result = validate_supersede_chains([orphan])
        assert not result.ok
        assert any(i.code == "SUPERSEDES_UNKNOWN" for i in result.issues)

    def test_foreign_key_supersede_is_blocking(self):
        m1 = _measurement(id="m1", experiment_run_id="e1", output_id="o1", revision=1)
        m2 = _measurement(
            id="m2",
            experiment_run_id="e2",
            output_id="o1",
            revision=2,
            supersedes_measurement_id="m1",
        )
        result = validate_supersede_chains([m1, m2])
        assert any(i.code == "SUPERSEDES_FOREIGN" for i in result.issues)

    def test_two_heads_for_one_key_is_blocking(self):
        a = _measurement(id="a", revision=1)
        b = _measurement(id="b", revision=1)
        result = validate_supersede_chains([a, b])
        assert any(i.code == "MULTIPLE_CHAIN_HEADS" for i in result.issues)

"""Measurement supersede-chain counterexamples enforced at the repository (§2.12)."""

import pytest

from backend.persistence import PersistenceError


class TestValidChain:
    """A contiguous, head-superseding chain is accepted."""

    def test_two_revisions_persist(self, seeded_experiment, make_measurement):
        repo = seeded_experiment
        repo.add_measurement(make_measurement(id="m1", revision=1))
        repo.add_measurement(
            make_measurement(id="m2", revision=2, supersedes_measurement_id="m1")
        )
        assert [m.id for m in repo.list_measurements("exp-1")] == ["m1", "m2"]


class TestMalformedChains:
    """Gaps, branches, cycles, and cross-key pointers must be rejected."""

    def test_first_revision_must_be_one(self, seeded_experiment, make_measurement):
        with pytest.raises(PersistenceError):
            seeded_experiment.add_measurement(
                make_measurement(id="m2", revision=2, supersedes_measurement_id="m1")
            )

    def test_gap_in_revisions_is_rejected(self, seeded_experiment, make_measurement):
        repo = seeded_experiment
        repo.add_measurement(make_measurement(id="m1", revision=1))
        with pytest.raises(PersistenceError):
            repo.add_measurement(
                make_measurement(id="m3", revision=3, supersedes_measurement_id="m1")
            )

    def test_branch_second_head_is_rejected(self, seeded_experiment, make_measurement):
        repo = seeded_experiment
        repo.add_measurement(make_measurement(id="m1", revision=1))
        with pytest.raises(PersistenceError):
            repo.add_measurement(make_measurement(id="m1b", revision=1))

    def test_superseding_a_non_head_is_rejected(
        self, seeded_experiment, make_measurement
    ):
        repo = seeded_experiment
        repo.add_measurement(make_measurement(id="m1", revision=1))
        repo.add_measurement(
            make_measurement(id="m2", revision=2, supersedes_measurement_id="m1")
        )
        with pytest.raises(PersistenceError):
            repo.add_measurement(
                make_measurement(id="m3", revision=3, supersedes_measurement_id="m1")
            )

    def test_cross_output_supersede_is_rejected(
        self, seeded_experiment, make_measurement
    ):
        repo = seeded_experiment
        repo.add_measurement(make_measurement(id="m1", output_id="o1", revision=1))
        with pytest.raises(PersistenceError):
            repo.add_measurement(
                make_measurement(
                    id="m2", output_id="o2", revision=2, supersedes_measurement_id="m1"
                )
            )


class TestTransactionRollback:
    """A failed unit of work leaves the store untouched."""

    def test_rollback_discards_writes(self, seeded_run, make_round, make_batch):
        repo = seeded_run
        with pytest.raises(RuntimeError):
            with repo.transaction():
                repo.add_batch(make_batch(id="batch-1", round_number=1))
                repo.add_round(make_round(id="round-1", round_number=1))
                raise RuntimeError("boom")
        assert repo.get_round("round-1") is None
        assert repo.get_batch("batch-1") is None

    def test_failed_measurement_insert_persists_nothing(
        self, seeded_run, make_measurement
    ):
        # No experiment_run 'exp-1' exists -> FK failure inside add_measurement.
        with pytest.raises(PersistenceError):
            seeded_run.add_measurement(make_measurement(id="m1", revision=1))
        assert seeded_run.get_measurement("m1") is None

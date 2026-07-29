"""Foreign-key counterexamples: orphan child rows must be rejected (§7)."""

import pytest

from backend.persistence import PersistenceError


class TestOrphanEntities:
    """Inserting a child with no parent violates a real FOREIGN KEY."""

    def test_orphan_revision_is_rejected(self, repo, make_revision):
        with pytest.raises(PersistenceError):
            repo.add_revision(make_revision())

    def test_orphan_run_is_rejected(self, repo, make_run):
        with pytest.raises(PersistenceError):
            repo.add_run(make_run())

    def test_orphan_round_is_rejected(self, repo, make_round):
        with pytest.raises(PersistenceError):
            repo.add_round(make_round())

    def test_orphan_experiment_run_is_rejected(self, seeded_run, make_experiment_run):
        with pytest.raises(PersistenceError):
            seeded_run.add_experiment_run(make_experiment_run())

    def test_orphan_measurement_is_rejected(self, seeded_run, make_measurement):
        with pytest.raises(PersistenceError):
            seeded_run.add_measurement(make_measurement())

    def test_orphan_batch_is_rejected(self, repo, make_batch):
        with pytest.raises(PersistenceError):
            repo.add_batch(make_batch())


class TestUniqueConstraints:
    """The round-number uniqueness constraints reject a second row per key."""

    def test_duplicate_round_number_is_rejected(self, seeded_run, make_round):
        seeded_run.add_round(make_round(id="round-1", round_number=1))
        with pytest.raises(PersistenceError):
            seeded_run.add_round(make_round(id="round-2", round_number=1))

    def test_duplicate_batch_round_number_is_rejected(self, seeded_run, make_batch):
        seeded_run.add_batch(make_batch(id="batch-1", round_number=1))
        with pytest.raises(PersistenceError):
            seeded_run.add_batch(make_batch(id="batch-2", round_number=1))

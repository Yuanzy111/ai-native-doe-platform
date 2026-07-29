"""Cross-aggregate ownership counterexamples enforced at the repository (§7).

Foreign keys guarantee a parent row exists, but not that a child and its parent
agree on the *owning* aggregate. These tests pin down the extra checks: a run's
revision must belong to its campaign, an experiment's round to its run, a round's
batch to its run and round number, a measurement's output to the pinned revision,
and no ``save_*`` may quietly rewrite a parent/ownership column.
"""

import pytest

from backend.domain import models as m
from backend.persistence import PersistenceError


class TestInsertOwnership:
    """Inserts must reject children whose parent belongs to another aggregate."""

    def test_run_revision_from_other_campaign_rejected(
        self, seeded_definition, repo, make_definition, make_revision, make_run
    ):
        with repo.transaction():
            repo.add_definition(
                make_definition(id="cd-2", name="Other", head_revision_id="rev-2")
            )
            repo.add_revision(make_revision(id="rev-2", campaign_definition_id="cd-2"))
        with pytest.raises(PersistenceError):
            repo.add_run(make_run(definition_revision_id="rev-2"))

    def test_run_unknown_revision_rejected(self, seeded_definition, repo, make_run):
        with pytest.raises(PersistenceError):
            repo.add_run(make_run(definition_revision_id="ghost"))

    def test_experiment_run_round_from_other_run_rejected(
        self, seeded_experiment, repo, make_run, make_batch, make_round, make_experiment_run
    ):
        # round-1 belongs to run-1; file an experiment under run-2 pointing at it.
        with repo.transaction():
            repo.add_run(make_run(id="run-2"))
        with pytest.raises(PersistenceError):
            repo.add_experiment_run(
                make_experiment_run(
                    id="exp-2", campaign_run_id="run-2", experiment_round_id="round-1"
                )
            )

    def test_round_unknown_batch_rejected(self, seeded_run, make_round):
        with pytest.raises(PersistenceError):
            seeded_run.add_round(
                make_round(id="round-1", recommendation_batch_id="ghost")
            )

    def test_round_batch_run_mismatch_rejected(
        self, seeded_run, repo, make_run, make_batch, make_round
    ):
        with repo.transaction():
            repo.add_run(make_run(id="run-2"))
            repo.add_batch(
                make_batch(id="batch-2", campaign_run_id="run-2", round_number=1)
            )
        with pytest.raises(PersistenceError):
            # round on run-1, batch on run-2.
            repo.add_round(
                make_round(id="round-1", recommendation_batch_id="batch-2")
            )

    def test_round_batch_number_mismatch_rejected(
        self, seeded_run, make_batch, make_round
    ):
        seeded_run.add_batch(make_batch(id="batch-1", round_number=2))
        with pytest.raises(PersistenceError):
            seeded_run.add_round(
                make_round(id="round-1", round_number=1, recommendation_batch_id="batch-1")
            )

    def test_measurement_output_not_in_revision_rejected(
        self, seeded_experiment, make_measurement
    ):
        # rev-1 declares only output 'o1'.
        with pytest.raises(PersistenceError):
            seeded_experiment.add_measurement(
                make_measurement(id="m1", output_id="o-ghost", revision=1)
            )


class TestSaveDoesNotRewriteParents:
    """save_* must refuse to change a parent/ownership/identity column."""

    def test_save_run_cannot_change_campaign(self, seeded_run, make_run):
        run = seeded_run.get_run("run-1")
        tampered = run.model_copy(update={"campaign_definition_id": "cd-2"})
        with pytest.raises(PersistenceError):
            seeded_run.save_run(tampered)

    def test_save_round_cannot_change_run(self, seeded_experiment):
        experiment_round = seeded_experiment.get_round("round-1")
        tampered = experiment_round.model_copy(update={"campaign_run_id": "run-2"})
        with pytest.raises(PersistenceError):
            seeded_experiment.save_round(tampered)

    def test_save_round_cannot_change_batch(self, seeded_experiment):
        experiment_round = seeded_experiment.get_round("round-1")
        tampered = experiment_round.model_copy(
            update={"recommendation_batch_id": "batch-2"}
        )
        with pytest.raises(PersistenceError):
            seeded_experiment.save_round(tampered)

    def test_save_experiment_run_cannot_change_round(self, seeded_experiment):
        experiment = seeded_experiment.get_experiment_run("exp-1")
        tampered = experiment.model_copy(update={"experiment_round_id": "round-2"})
        with pytest.raises(PersistenceError):
            seeded_experiment.save_experiment_run(tampered)

    def test_save_batch_cannot_change_run(self, seeded_experiment):
        batch = seeded_experiment.get_batch("batch-1")
        tampered = batch.model_copy(update={"campaign_run_id": "run-2"})
        with pytest.raises(PersistenceError):
            seeded_experiment.save_batch(tampered)

    def test_save_batch_cannot_change_round_number(self, seeded_experiment):
        batch = seeded_experiment.get_batch("batch-1")
        tampered = batch.model_copy(update={"round_number": 2})
        with pytest.raises(PersistenceError):
            seeded_experiment.save_batch(tampered)

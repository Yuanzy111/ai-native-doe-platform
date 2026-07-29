"""Tests for immutable revision behavior (§2.2, §7).

Immutability is enforced at two layers: the frozen Pydantic model blocks
in-place mutation, and the persistence boundary enforces the append-only,
monotonic, correctly-parented revision chain.
"""

import pytest
from pydantic import ValidationError

from backend.domain import models as m
from backend.persistence import PersistenceError


class TestModelImmutability:
    """A revision (and its nested value objects) cannot be mutated in place."""

    def test_setattr_on_revision_is_blocked(self, make_revision):
        revision = make_revision()
        with pytest.raises(ValidationError):
            revision.revision_number = 2

    def test_setattr_on_nested_parameter_is_blocked(self, make_revision):
        revision = make_revision()
        with pytest.raises(ValidationError):
            revision.parameters[0].name = "Renamed"

    def test_evolving_produces_a_new_object(self, make_revision):
        revision = make_revision()
        evolved = revision.model_copy(update={"id": "rev-2"})
        assert revision.id == "rev-1"
        assert evolved.id == "rev-2"
        assert evolved is not revision

    def test_first_revision_must_not_have_parent(self, make_revision):
        with pytest.raises(ValidationError):
            make_revision(revision_number=1, parent_revision_id="rev-0")

    def test_later_revision_must_have_parent(self, make_revision):
        with pytest.raises(ValidationError):
            make_revision(revision_number=2, parent_revision_id=None)


class TestPersistedRevisionChain:
    """The repository enforces the append-only monotonic revision chain."""

    def test_first_revision_persists_and_round_trips(self, repo, make_definition, make_revision):
        repo.add_definition(make_definition())
        revision = make_revision()
        repo.add_revision(revision)
        assert repo.get_revision("rev-1") == revision

    def test_sequential_revisions_are_appended(self, repo, make_definition, make_revision):
        repo.add_definition(make_definition())
        repo.add_revision(make_revision())
        repo.add_revision(make_revision(id="rev-2", revision_number=2, parent_revision_id="rev-1"))
        assert [r.id for r in repo.list_revisions("cd-1")] == ["rev-1", "rev-2"]

    def test_non_sequential_revision_number_is_rejected(self, repo, make_definition, make_revision):
        repo.add_definition(make_definition())
        repo.add_revision(make_revision())
        with pytest.raises(PersistenceError):
            repo.add_revision(make_revision(id="rev-3", revision_number=3, parent_revision_id="rev-1"))

    def test_wrong_parent_linkage_is_rejected(self, repo, make_definition, make_revision):
        repo.add_definition(make_definition())
        repo.add_revision(make_revision())
        with pytest.raises(PersistenceError):
            repo.add_revision(make_revision(id="rev-2", revision_number=2, parent_revision_id="rev-0"))

    def test_reinserting_a_revision_id_is_rejected(self, repo, make_definition, make_revision):
        repo.add_definition(make_definition())
        repo.add_revision(make_revision())
        with pytest.raises(PersistenceError):
            repo.add_revision(make_revision())

    def test_repository_exposes_no_revision_update(self, repo):
        assert not hasattr(repo, "save_revision")
        assert not hasattr(repo, "update_revision")
        assert not hasattr(repo, "delete_revision")

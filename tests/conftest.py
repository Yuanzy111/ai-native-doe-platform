"""Shared test configuration and builders for the backend domain suite.

Inserts the repository root onto ``sys.path`` so ``import backend`` resolves
when pytest is run from the project root, and exposes small factory fixtures so
individual tests can build valid domain objects and override only the field
under test (avoiding duplicated construction boilerplate).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.domain import models as m  # noqa: E402  (after sys.path setup)
from backend.persistence import SqliteRepository  # noqa: E402


def _revision(**overrides) -> m.CampaignDefinitionRevision:
    """Build a valid, confirmed single-objective revision with two parameters."""
    defaults = dict(
        id="rev-1",
        campaign_definition_id="cd-1",
        revision_number=1,
        parent_revision_id=None,
        parameters=[
            m.ContinuousParameterSpec(
                id="resin", name="Resin Ratio", bounds=m.Bounds(lower=0, upper=100)
            ),
            m.ContinuousParameterSpec(
                id="hard", name="Hardener Ratio", bounds=m.Bounds(lower=0, upper=100)
            ),
        ],
        outputs=[m.OutputSpec(id="o1", name="Strength")],
        targets=[m.TargetSpec(id="t1", output_id="o1", direction="Maximize")],
        objective_policy=m.SingleObjectivePolicy(target_id="t1"),
        constraints=[],
        constraints_confirmed=True,
        constraints_confirmed_at="2026-07-29T00:00:00Z",
        created_at="2026-07-29T00:00:00Z",
        created_by="user-1",
    )
    defaults.update(overrides)
    return m.CampaignDefinitionRevision(**defaults)


def _definition(**overrides) -> m.CampaignDefinition:
    """Build a valid campaign definition container."""
    defaults = dict(
        id="cd-1",
        name="Epoxy Coating Optimization",
        head_revision_id="rev-1",
        created_at="2026-07-29T00:00:00Z",
        created_by="user-1",
        updated_at="2026-07-29T00:00:00Z",
    )
    defaults.update(overrides)
    return m.CampaignDefinition(**defaults)


def _run(**overrides) -> m.CampaignRun:
    """Build a valid campaign run in the Draft state."""
    defaults = dict(
        id="run-1",
        campaign_definition_id="cd-1",
        definition_revision_id="rev-1",
        status=m.RunStatus.DRAFT,
        optimization_policy=m.OptimizationPolicy(
            id="op-1",
            batch_size=4,
            seed_policy=m.SeedPolicy.FIXED,
            seed_value=42,
            strategy_config=m.BotorchConfig(acquisition_function="qLogEI"),
        ),
        round=0,
        budget_total=10,
        budget_used=0,
        created_at="2026-07-29T00:00:00Z",
        updated_at="2026-07-29T00:00:00Z",
        created_by="user-1",
    )
    defaults.update(overrides)
    return m.CampaignRun(**defaults)


@pytest.fixture
def make_revision():
    """Return the revision factory."""
    return _revision


@pytest.fixture
def make_definition():
    """Return the definition factory."""
    return _definition


@pytest.fixture
def make_run():
    """Return the run factory."""
    return _run


@pytest.fixture
def repo():
    """Yield a fresh in-memory SQLite repository."""
    repository = SqliteRepository.connect(":memory:")
    yield repository
    repository.close()

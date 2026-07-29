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

from backend.application import ApplicationService  # noqa: E402
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


def _round(**overrides) -> m.ExperimentRound:
    """Build a valid open experiment round."""
    defaults = dict(
        id="round-1",
        campaign_run_id="run-1",
        round_number=1,
        recommendation_batch_id="batch-1",
        experiment_run_ids=[],
        opened_at="2026-07-29T00:00:00Z",
        status=m.RoundStatus.OPEN,
    )
    defaults.update(overrides)
    return m.ExperimentRound(**defaults)


def _experiment_run(**overrides) -> m.ExperimentRun:
    """Build a valid completed experiment run."""
    defaults = dict(
        id="exp-1",
        campaign_run_id="run-1",
        experiment_round_id="round-1",
        parameter_values={"resin": 60.0, "hard": 40.0},
        status=m.ExperimentRunStatus.COMPLETED,
        executed_at="2026-07-29T01:00:00Z",
        executed_by="user-1",
    )
    defaults.update(overrides)
    return m.ExperimentRun(**defaults)


def _measurement(**overrides) -> m.Measurement:
    """Build a valid first-revision measurement."""
    defaults = dict(
        id="meas-1",
        experiment_run_id="exp-1",
        output_id="o1",
        value=76.0,
        status=m.MeasurementStatus.VALID,
        revision=1,
        supersedes_measurement_id=None,
        recorded_at="2026-07-29T02:00:00Z",
        recorded_by="user-1",
    )
    defaults.update(overrides)
    return m.Measurement(**defaults)


def _batch(**overrides) -> m.RecommendationBatch:
    """Build a valid single-candidate recommendation batch."""
    defaults = dict(
        id="batch-1",
        campaign_run_id="run-1",
        round_number=1,
        generated_at="2026-07-29T00:30:00Z",
        input_snapshot={},
        algorithm_config=m.AlgorithmConfig(
            backend_name="baybe",
            backend_version="0.13.0",
            backend_commit="deadbeef",
            strategy_kind="Botorch",
            acquisition_function="qLogEI",
            seed=42,
            environment=m.Environment(
                python_version="3.11.15",
                torch_version="2.4.0",
                botorch_version="0.11.0",
                dependency_lock_hash="sha256:0",
            ),
        ),
        candidates=[
            m.RecommendationCandidate(
                id="cand-1", parameter_values={"resin": 60.0, "hard": 40.0}
            )
        ],
        status=m.BatchStatus.PROPOSED,
    )
    defaults.update(overrides)
    return m.RecommendationBatch(**defaults)


@pytest.fixture
def make_round():
    """Return the experiment-round factory."""
    return _round


@pytest.fixture
def make_experiment_run():
    """Return the experiment-run factory."""
    return _experiment_run


@pytest.fixture
def make_measurement():
    """Return the measurement factory."""
    return _measurement


@pytest.fixture
def make_batch():
    """Return the recommendation-batch factory."""
    return _batch


@pytest.fixture
def repo():
    """Yield a fresh in-memory SQLite repository."""
    repository = SqliteRepository.connect(":memory:")
    yield repository
    repository.close()


@pytest.fixture
def service(repo):
    """Return an application service over a fresh repository."""
    return ApplicationService(repo)


@pytest.fixture
def seeded_definition(repo):
    """Seed a definition and its first revision (``cd-1`` / ``rev-1``); no run."""
    with repo.transaction():
        repo.add_definition(_definition())
        repo.add_revision(_revision())
    return repo


@pytest.fixture
def seeded_run(repo):
    """Seed a definition, first revision, and Draft run; return the repository.

    Provides the full foreign-key graph (``cd-1`` / ``rev-1`` / ``run-1``) that
    child entities (rounds, experiment runs, measurements, batches) depend on.
    """
    with repo.transaction():
        repo.add_definition(_definition())
        repo.add_revision(_revision())
        repo.add_run(_run())
    return repo


@pytest.fixture
def seeded_experiment(seeded_run):
    """Extend :func:`seeded_run` with a batch, a round, and one experiment run.

    A round is tied to its originating recommendation batch, so ``batch-1`` is
    seeded before ``round-1`` and the experiment run ``exp-1``.
    """
    with seeded_run.transaction():
        seeded_run.add_batch(_batch())
        seeded_run.add_round(_round())
        seeded_run.add_experiment_run(_experiment_run())
    return seeded_run

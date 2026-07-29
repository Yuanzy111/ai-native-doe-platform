"""Read-only aggregate views for the API (architecture v0.2, §6).

The router never assembles cross-aggregate business state itself; it asks this
service for a ready-made view. Everything here is read-only — no transaction, no
mutation — and every model is dumped to the same ``camelCase`` JSON shape used
everywhere else, so a client (the Workbench) can restore page state from one
response.
"""

from __future__ import annotations

from typing import Any

from backend.application import EntityNotFoundError
from backend.domain.models import CampaignRun, RecommendationBatch
from backend.persistence import SqliteRepository


def _dump(model: Any) -> Any:
    """Serialize a domain model to camelCase JSON, or pass ``None`` through."""
    if model is None:
        return None
    return model.model_dump(mode="json", by_alias=True)


class RunQueryService:
    """Assembles the read-side views over a :class:`SqliteRepository`."""

    def __init__(self, repository: SqliteRepository) -> None:
        self._repo = repository

    def require_run(self, run_id: str) -> CampaignRun:
        """Return a run or raise :class:`EntityNotFoundError` (maps to 404)."""
        run = self._repo.get_run(run_id)
        if run is None:
            raise EntityNotFoundError(f"Unknown run {run_id!r}.")
        return run

    def run_view(self, run_id: str) -> dict[str, Any]:
        """Return the full Workbench aggregate for one run.

        Bundles the campaign definition, the pinned revision, the run, and every
        child entity (batches, rounds, experiment runs, measurements, decision
        logs) needed to restore the page.
        """
        run = self.require_run(run_id)
        definition = self._repo.get_definition(run.campaign_definition_id)
        revision = self._repo.get_revision(run.definition_revision_id)
        experiment_runs = self._repo.list_experiment_runs_for_run(run_id)
        measurements: list[Any] = []
        for experiment in experiment_runs:
            measurements.extend(self._repo.list_measurements(experiment.id))
        return {
            "campaignDefinition": _dump(definition),
            "pinnedRevision": _dump(revision),
            "campaignRun": _dump(run),
            "recommendationBatches": [
                _dump(batch) for batch in self._repo.list_batches(run_id)
            ],
            "experimentRounds": [
                _dump(round_) for round_ in self._repo.list_rounds(run_id)
            ],
            "experimentRuns": [_dump(experiment) for experiment in experiment_runs],
            "measurements": [_dump(measurement) for measurement in measurements],
            "decisionLogs": [
                _dump(log) for log in self._repo.list_decision_logs(run_id)
            ],
        }

    def initial_design_view(
        self, run_id: str, batch: RecommendationBatch
    ) -> dict[str, Any]:
        """Return the run, batch, its open round, and that round's experiments.

        Used as the response for ``POST /{runId}/initial-design`` so the caller
        sees exactly what one atomic generation produced.
        """
        run = self.require_run(run_id)
        experiment_round = next(
            (
                candidate_round
                for candidate_round in self._repo.list_rounds(run_id)
                if candidate_round.recommendation_batch_id == batch.id
            ),
            None,
        )
        experiments = (
            self._repo.list_experiment_runs(experiment_round.id)
            if experiment_round is not None
            else []
        )
        return {
            "campaignRun": _dump(run),
            "recommendationBatch": _dump(batch),
            "experimentRound": _dump(experiment_round),
            "experimentRuns": [_dump(experiment) for experiment in experiments],
        }


__all__ = ["RunQueryService"]

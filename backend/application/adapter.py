"""The optimizer-adapter boundary (architecture v0.2, §4.1).

The application service never talks to an optimization backend directly; it
depends only on the :class:`OptimizerAdapter` protocol defined here. An adapter
turns a definition revision and an :class:`~backend.domain.models.OptimizationPolicy`
into a :class:`RecommendationResult` — the candidates plus the resolved,
reproducible algorithm configuration and the self-contained input snapshot the
service persists onto a :class:`~backend.domain.models.RecommendationBatch`.

The production :class:`~backend.adapters.baybe.BayBEAdapter` implements the
initial-design leg of this protocol against real BayBE; a lightweight fake in
the test suite covers the later legs so the closed loop can still be exercised
end-to-end without pulling in a heavyweight optimizer.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from backend.domain.models import (
    AlgorithmConfig,
    CampaignDefinitionRevision,
    OptimizationPolicy,
    RecommendationCandidate,
)


@dataclass(frozen=True)
class RecommendationResult:
    """The adapter's output for one recommendation request (§4.1).

    Attributes:
        candidates: The proposed candidates; the service validates them with
            :func:`~backend.domain.validation.validate_candidates` before a batch
            is assembled.
        algorithm_config: The resolved, reproducible algorithm configuration
            recorded verbatim on the persisted batch.
        input_snapshot: The deep, self-contained inputs used to produce the
            candidates, recorded on the batch for reproducibility.
    """

    candidates: list[RecommendationCandidate]
    algorithm_config: AlgorithmConfig
    input_snapshot: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class OptimizerAdapter(Protocol):
    """The boundary the application service depends on to obtain candidates.

    Implementations must be pure with respect to persistence: they receive the
    definition revision and policy and return a :class:`RecommendationResult`,
    leaving all batch/round/state persistence to the service so the whole step
    stays in one transaction.
    """

    def generate_initial_design(
        self,
        revision: CampaignDefinitionRevision,
        policy: OptimizationPolicy,
    ) -> RecommendationResult:
        """Return the model-free initial design for a run's first round (§4.1)."""
        ...


__all__ = ["OptimizerAdapter", "RecommendationResult"]

"""Optimizer-backend adapters (architecture v0.2, §4/§5).

Each adapter turns a :class:`~backend.domain.models.CampaignDefinitionRevision`
plus an :class:`~backend.domain.models.OptimizationPolicy` into a
:class:`~backend.application.adapter.RecommendationResult`, translating every
backend-specific failure into one of the adapter-boundary errors defined in
:mod:`backend.adapters.errors`. The application service depends only on the
:class:`~backend.application.adapter.OptimizerAdapter` protocol, never on a
concrete backend.
"""

from backend.adapters.errors import (
    AdapterComputationError,
    AdapterError,
    AdapterValidationError,
    UnsupportedFeatureError,
)

__all__ = [
    "AdapterComputationError",
    "AdapterError",
    "AdapterValidationError",
    "UnsupportedFeatureError",
]

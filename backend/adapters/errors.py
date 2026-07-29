"""Adapter-boundary error taxonomy (architecture v0.2, §4).

Every optimizer adapter must fail with one of these three errors so the
application service — and everything above it — can react to failures without
knowing which backend produced them. Raw backend exceptions (BayBE/BoTorch
internals) must never cross the adapter boundary; they are caught and
re-raised as one of these types.
"""


class AdapterError(Exception):
    """Base class for every adapter-boundary failure."""


class AdapterValidationError(AdapterError):
    """The inputs are structurally unusable for this backend.

    Raised when a definition/policy cannot be turned into a valid backend
    problem — e.g. a constraint references an unknown parameter, or a resolved
    search space is empty. Distinct from :class:`UnsupportedFeatureError`,
    which marks inputs that are *valid* but not *supported*.
    """


class UnsupportedFeatureError(AdapterError):
    """The inputs are valid but ask for something this backend cannot express.

    Raised for feature gaps that must be surfaced rather than silently
    downgraded — e.g. a mixed continuous/discrete linear constraint, a discrete
    linear constraint with non-unit coefficients, an initial recommender that
    is incompatible with the resolved search space, or a strategy that provides
    no cold-start phase for the initial design.
    """


class AdapterComputationError(AdapterError):
    """The backend failed while computing a recommendation.

    Raised when the search space and recommender were built successfully but
    the backend raised during recommendation, or returned a result that
    violates the adapter's output contract (e.g. the wrong number of rows).
    """


__all__ = [
    "AdapterComputationError",
    "AdapterError",
    "AdapterValidationError",
    "UnsupportedFeatureError",
]

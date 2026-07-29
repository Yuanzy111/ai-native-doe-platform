"""The production BayBE optimizer adapter (architecture v0.2, §4/§5).

This adapter implements the *initial-design* leg of the loop only: it turns a
:class:`~backend.domain.models.CampaignDefinitionRevision` plus an
:class:`~backend.domain.models.OptimizationPolicy` into a model-free
:class:`~backend.application.adapter.RecommendationResult` using real BayBE. It
never persists anything and never mutates the run — the application service owns
the transaction that writes the batch, round, and experiment runs.

Deliberately out of scope for this pass (raise rather than pretend):

* ``recommend`` / model fitting / any later round.
* Any objective is *not* built here — the initial design is model-free, so no
  predicted means, uncertainties, or desirabilities are ever fabricated.

Every backend-specific failure is translated into one of the adapter-boundary
errors in :mod:`backend.adapters.errors`; raw BayBE exceptions never escape.
"""

from __future__ import annotations

import hashlib
import platform
import random
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from baybe.constraints import (
    ContinuousCardinalityConstraint,
    ContinuousLinearConstraint,
    DiscreteCardinalityConstraint,
    DiscreteSumConstraint,
    ThresholdCondition,
)
from baybe.parameters import (
    CategoricalParameter,
    NumericalContinuousParameter,
    NumericalDiscreteParameter,
)
from baybe.recommenders import FPSRecommender, RandomRecommender
from baybe.searchspace import SearchSpace, SearchSpaceType
from baybe.settings import Settings

from backend.adapters.errors import (
    AdapterComputationError,
    AdapterError,
    AdapterValidationError,
    UnsupportedFeatureError,
)
from backend.application.adapter import RecommendationResult
from backend.domain.models import (
    AlgorithmConfig,
    CampaignDefinitionRevision,
    CardinalityConstraintSpec,
    CategoricalParameterSpec,
    ContinuousParameterSpec,
    DiscreteParameterSpec,
    Environment,
    LinearEqualityConstraintSpec,
    LinearInequalityConstraintSpec,
    OptimizationPolicy,
    ParameterSpec,
    RecommendationCandidate,
    SeedPolicy,
    TwoPhaseMetaConfig,
)

_SEED_UPPER_BOUND = 2**31
_BACKEND_NAME = "baybe"


class BayBEAdapter:
    """A real-BayBE :class:`~backend.application.adapter.OptimizerAdapter`.

    Only :meth:`generate_initial_design` is implemented; the recommend/update
    legs of the protocol are intentionally absent until a model-fitting pass is
    scoped.
    """

    def generate_initial_design(
        self,
        revision: CampaignDefinitionRevision,
        policy: OptimizationPolicy,
    ) -> RecommendationResult:
        """Return the model-free initial design for a run's first round (§4.1).

        Args:
            revision: The validated design-space definition to sample from.
            policy: The run's execution policy (batch size, seed, strategy).

        Returns:
            A :class:`RecommendationResult` whose candidates carry no predicted
            objectives (the initial design is model-free) and whose
            ``algorithm_config``/``input_snapshot`` record everything needed to
            reproduce the draw.

        Raises:
            AdapterValidationError: The definition cannot be turned into a valid
                BayBE problem.
            UnsupportedFeatureError: The definition/policy asks for something
                BayBE cannot express here (unsupported constraint shape,
                incompatible initial recommender, or a strategy with no
                cold-start phase).
            AdapterComputationError: BayBE failed while sampling, or returned the
                wrong number of candidates.
        """
        if policy.backend_name != _BACKEND_NAME:
            raise AdapterValidationError(
                f"This adapter only serves the {_BACKEND_NAME!r} backend, but the "
                f"policy requests backend {policy.backend_name!r}. Refusing to run "
                "BayBE for another backend's policy."
            )
        specs_by_id = {spec.id: spec for spec in revision.parameters}
        searchspace = self._build_searchspace(revision, specs_by_id)
        recommender, recommender_name = self._select_recommender(policy, searchspace)
        seed = self._resolve_seed(policy)

        try:
            with Settings(random_seed=seed):
                frame = recommender.recommend(
                    batch_size=policy.batch_size, searchspace=searchspace
                )
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 — translate at the boundary
            raise AdapterComputationError(
                f"BayBE failed to produce an initial design: {exc}"
            ) from exc

        if len(frame) != policy.batch_size:
            raise AdapterComputationError(
                f"BayBE returned {len(frame)} candidates but the policy batchSize "
                f"is {policy.batch_size}; the search space is too small."
            )

        candidates = [
            RecommendationCandidate(
                id=str(uuid.uuid4()),
                parameter_values={
                    spec.id: _to_native(spec, row[spec.id])
                    for spec in revision.parameters
                },
            )
            for _, row in frame.iterrows()
        ]

        algorithm_config = self._build_algorithm_config(policy, seed)
        input_snapshot = self._build_input_snapshot(
            revision, policy, seed, recommender_name
        )
        return RecommendationResult(
            candidates=candidates,
            algorithm_config=algorithm_config,
            input_snapshot=input_snapshot,
        )

    # Search space -----------------------------------------------------------

    def _build_searchspace(
        self,
        revision: CampaignDefinitionRevision,
        specs_by_id: dict[str, ParameterSpec],
    ) -> SearchSpace:
        """Map parameters + constraints to a BayBE :class:`SearchSpace`."""
        try:
            parameters = [_build_parameter(spec) for spec in revision.parameters]
            constraints = [
                _build_constraint(constraint, specs_by_id)
                for constraint in revision.constraints
            ]
            searchspace = SearchSpace.from_product(parameters, constraints)
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 — translate at the boundary
            raise AdapterValidationError(
                f"Failed to build a BayBE search space: {exc}"
            ) from exc
        return searchspace

    # Recommender ------------------------------------------------------------

    def _select_recommender(
        self, policy: OptimizationPolicy, searchspace: SearchSpace
    ) -> tuple[Any, str]:
        """Pick the cold-start recommender from the strategy config.

        Only :class:`TwoPhaseMetaConfig` carries a cold-start phase. A direct
        Botorch strategy has no initial recommender, so an initial design cannot
        be produced from it — that is rejected rather than silently defaulted.
        """
        strategy = policy.strategy_config
        if not isinstance(strategy, TwoPhaseMetaConfig):
            raise UnsupportedFeatureError(
                "The initial design needs a cold-start recommender, but the "
                f"policy strategy is {strategy.kind!r} with no initial phase. "
                "Configure a TwoPhaseMeta strategy with an initial recommender."
            )
        name = strategy.initial_recommender
        if name == "RandomRecommender":
            return RandomRecommender(), name
        if name == "FPSRecommender":
            if searchspace.type is not SearchSpaceType.DISCRETE:
                raise UnsupportedFeatureError(
                    "FPSRecommender requires a fully discrete search space, but "
                    f"the resolved space is {searchspace.type.value!r}. Refusing "
                    "to silently fall back to a different recommender."
                )
            return FPSRecommender(), name
        raise UnsupportedFeatureError(
            f"Unsupported initial recommender {name!r}."
        )

    # Reproducibility --------------------------------------------------------

    def _resolve_seed(self, policy: OptimizationPolicy) -> int:
        """Return the concrete seed, generating one for ``AutoGenerated``."""
        if policy.seed_policy is SeedPolicy.FIXED:
            # Domain validation guarantees a non-null seed_value here.
            return int(policy.seed_value)
        return random.SystemRandom().randrange(_SEED_UPPER_BOUND)

    def _build_algorithm_config(
        self, policy: OptimizationPolicy, seed: int
    ) -> AlgorithmConfig:
        """Record the resolved, reproducible algorithm configuration."""
        strategy = policy.strategy_config
        return AlgorithmConfig(
            backend_name=_BACKEND_NAME,
            backend_version=_baybe_version(),
            backend_commit=_baybe_commit(),
            strategy_kind=strategy.kind,
            hyperparameters=strategy.model_dump(mode="json", by_alias=True),
            acquisition_function=strategy.acquisition_function,
            seed=seed,
            environment=_environment(),
        )

    def _build_input_snapshot(
        self,
        revision: CampaignDefinitionRevision,
        policy: OptimizationPolicy,
        seed: int,
        recommender_name: str,
    ) -> dict[str, Any]:
        """Capture a self-contained, JSON-serializable snapshot of the inputs."""
        return {
            "revisionId": revision.id,
            "revisionNumber": revision.revision_number,
            "policyId": policy.id,
            "backendName": _BACKEND_NAME,
            "batchSize": policy.batch_size,
            "seed": seed,
            "recommender": recommender_name,
            "parameters": [
                spec.model_dump(mode="json", by_alias=True)
                for spec in revision.parameters
            ],
            "constraints": [
                constraint.model_dump(mode="json", by_alias=True)
                for constraint in revision.constraints
            ],
            "objectivePolicy": revision.objective_policy.model_dump(
                mode="json", by_alias=True
            ),
        }


# Parameter mapping ---------------------------------------------------------


def _build_parameter(spec: ParameterSpec) -> Any:
    """Map one :class:`ParameterSpec` to its BayBE parameter.

    The stable ``spec.id`` — never the mutable display name — is used as the
    BayBE column name so downstream candidate/measurement joins stay correct.
    """
    if isinstance(spec, ContinuousParameterSpec):
        if spec.bounds.stepsize is not None:
            raise UnsupportedFeatureError(
                f"Continuous parameter {spec.id!r} declares a stepsize; BayBE's "
                "continuous parameter has no stepped variant. Model it as a "
                "Discrete parameter instead of silently dropping the step."
            )
        return NumericalContinuousParameter(
            name=spec.id, bounds=(spec.bounds.lower, spec.bounds.upper)
        )
    if isinstance(spec, DiscreteParameterSpec):
        return NumericalDiscreteParameter(
            name=spec.id, values=[float(value) for value in spec.values]
        )
    if isinstance(spec, CategoricalParameterSpec):
        return CategoricalParameter(name=spec.id, values=list(spec.values))
    raise UnsupportedFeatureError(
        f"Unsupported parameter type {type(spec).__name__!r}."
    )


# Constraint mapping --------------------------------------------------------


def _build_constraint(
    constraint: Any, specs_by_id: dict[str, ParameterSpec]
) -> Any:
    """Map one constraint spec to its BayBE constraint, or fail explicitly.

    Silently dropping a constraint would let the backend recommend points that
    violate a confirmed constraint, so every unsupported shape raises.
    """
    if isinstance(constraint, (LinearEqualityConstraintSpec, LinearInequalityConstraintSpec)):
        return _build_linear_constraint(constraint, specs_by_id)
    if isinstance(constraint, CardinalityConstraintSpec):
        return _build_cardinality_constraint(constraint, specs_by_id)
    raise UnsupportedFeatureError(
        f"Unsupported constraint kind {getattr(constraint, 'kind', type(constraint).__name__)!r}."
    )


def _parameter_kinds(
    parameter_ids: list[str], specs_by_id: dict[str, ParameterSpec]
) -> set[type]:
    """Return the distinct parameter-spec types referenced by a constraint."""
    kinds: set[type] = set()
    for pid in parameter_ids:
        spec = specs_by_id.get(pid)
        if spec is None:
            raise AdapterValidationError(
                f"Constraint references unknown parameter {pid!r}."
            )
        kinds.add(type(spec))
    return kinds


def _build_linear_constraint(
    constraint: Any, specs_by_id: dict[str, ParameterSpec]
) -> Any:
    """Map a linear (in)equality to a BayBE continuous-linear/discrete-sum form."""
    parameter_ids = list(constraint.parameter_ids)
    coefficients = [float(c) for c in constraint.coefficients]
    is_equality = isinstance(constraint, LinearEqualityConstraintSpec)
    operator = "=" if is_equality else constraint.operator
    kinds = _parameter_kinds(parameter_ids, specs_by_id)

    if kinds == {ContinuousParameterSpec}:
        return ContinuousLinearConstraint(
            parameters=parameter_ids,
            operator=operator,
            coefficients=coefficients,
            rhs=float(constraint.rhs),
        )
    if kinds == {DiscreteParameterSpec}:
        if not all(c == 1.0 for c in coefficients):
            raise UnsupportedFeatureError(
                f"Linear constraint {constraint.id!r} over discrete parameters is "
                "only supported as a sum constraint (all coefficients == 1); "
                f"got coefficients {coefficients}."
            )
        return DiscreteSumConstraint(
            parameters=parameter_ids,
            condition=ThresholdCondition(
                threshold=float(constraint.rhs), operator=operator
            ),
        )
    raise UnsupportedFeatureError(
        f"Linear constraint {constraint.id!r} spans a mix of parameter types "
        f"({sorted(k.__name__ for k in kinds)}); only all-continuous or "
        "all-numerical-discrete linear constraints are supported."
    )


def _build_cardinality_constraint(
    constraint: CardinalityConstraintSpec, specs_by_id: dict[str, ParameterSpec]
) -> Any:
    """Map a cardinality constraint to its continuous/discrete BayBE form."""
    parameter_ids = list(constraint.parameter_ids)
    kinds = _parameter_kinds(parameter_ids, specs_by_id)
    if kinds == {ContinuousParameterSpec}:
        return ContinuousCardinalityConstraint(
            parameters=parameter_ids,
            min_cardinality=constraint.min_cardinality,
            max_cardinality=constraint.max_cardinality,
        )
    if kinds == {DiscreteParameterSpec}:
        return DiscreteCardinalityConstraint(
            parameters=parameter_ids,
            min_cardinality=constraint.min_cardinality,
            max_cardinality=constraint.max_cardinality,
        )
    raise UnsupportedFeatureError(
        f"Cardinality constraint {constraint.id!r} spans a mix of parameter "
        f"types ({sorted(k.__name__ for k in kinds)}); only all-continuous or "
        "all-numerical-discrete cardinality constraints are supported."
    )


# Output conversion ---------------------------------------------------------


def _to_native(spec: ParameterSpec, value: Any) -> str | float:
    """Convert a BayBE cell (often a NumPy scalar) to a native Python value."""
    if isinstance(spec, CategoricalParameterSpec):
        return str(value)
    return float(value)


# Environment / provenance --------------------------------------------------


def _dist_version(package: str, module: Any = None) -> str:
    """Best-effort distribution version, falling back to ``module.__version__``."""
    try:
        return version(package)
    except PackageNotFoundError:
        return str(getattr(module, "__version__", "0+unknown"))


def _baybe_version() -> str:
    """Best-effort BayBE version (the vendored clone ships no metadata)."""
    import baybe

    return _dist_version("baybe", baybe)


def _baybe_commit() -> str:
    """Best-effort git commit of the (vendored) BayBE source; ``unknown`` if N/A."""
    try:
        import baybe

        clone_root = Path(baybe.__path__[0]).resolve().parent
        head = (clone_root / ".git" / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            return (clone_root / ".git" / ref).read_text().strip()
        return head
    except Exception:  # noqa: BLE001 — provenance is best-effort
        return "unknown"


def _lock_hash() -> str:
    """Hash of the pinned lock file for reproducibility provenance."""
    lock = Path(__file__).resolve().parents[2] / "requirements.lock"
    if lock.exists():
        return "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest()
    return "sha256:unavailable"


def _environment() -> Environment:
    """Capture the reproducibility environment recorded on the batch."""
    import botorch
    import torch

    return Environment(
        python_version=platform.python_version(),
        torch_version=_dist_version("torch", torch),
        botorch_version=_dist_version("botorch", botorch),
        dependency_lock_hash=_lock_hash(),
    )


__all__ = ["BayBEAdapter"]

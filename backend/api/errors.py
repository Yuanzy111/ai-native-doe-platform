"""Unified HTTP error contract for the API (architecture v0.2, §4/§6).

Every error the API returns has the same JSON shape::

    {"code": "STABLE_ERROR_CODE", "message": "...", "details": {}}

so a client can branch on ``code`` without parsing prose. The handlers here are
the *only* place backend exceptions are turned into responses: domain, service,
persistence, and adapter exceptions are mapped to a stable status/code, and no
raw traceback or SQLite/BayBE internal message is ever surfaced verbatim beyond
the mapped, human-readable ``message``.

Status mapping (§ requirements):

* request structure invalid          -> 422 ``VALIDATION_ERROR``
* resource not found                  -> 404 ``NOT_FOUND``
* state conflict / duplicate action   -> 409 ``CONFLICT``
* adapter cannot express the config   -> 422 ``UNSUPPORTED_FEATURE`` /
                                             ``ADAPTER_VALIDATION_ERROR``
* backend computation failed          -> 502 ``ADAPTER_COMPUTATION_FAILED``
* anything unexpected                 -> 500 ``INTERNAL_ERROR``
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.adapters.errors import (
    AdapterComputationError,
    AdapterError,
    AdapterValidationError,
    UnsupportedFeatureError,
)
from backend.agent.errors import (
    AgentActionRejectedError,
    AgentModelError,
    AgentNotConfiguredError,
    InvalidAgentOutputError,
    StaleAgentProposalError,
)
from backend.application import EntityNotFoundError, ServiceError
from backend.domain.validation import StateTransitionError
from backend.persistence import PersistenceError

_logger = logging.getLogger("backend.api")

_ADAPTER_COMPUTATION_MESSAGE = (
    "The optimization backend failed to generate a design."
)
_ADAPTER_GENERIC_MESSAGE = "The optimization backend could not fulfill the request."


class ApiError(Exception):
    """An error the API raises directly, already carrying its HTTP contract."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _payload(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    """Assemble the uniform error body."""
    return {"code": code, "message": message, "details": details}


def _json(
    status_code: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    """Build a :class:`JSONResponse` in the uniform error shape."""
    return JSONResponse(
        status_code=status_code, content=_payload(code, message, details or {})
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install every backend-exception -> HTTP mapping on ``app``.

    Handlers are matched by the exception's MRO (most specific first), so the
    adapter/service subclasses below take precedence over their bases.
    """

    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _json(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _json(
            422,
            "VALIDATION_ERROR",
            "The request body or parameters are invalid.",
            {"errors": _validation_errors(exc.errors())},
        )

    @app.exception_handler(ValidationError)
    async def _handle_pydantic_validation(
        _: Request, exc: ValidationError
    ) -> JSONResponse:
        # A domain model rejected server-assembled data (e.g. an out-of-range
        # field the request schema did not already bound).
        return _json(
            422,
            "VALIDATION_ERROR",
            "The request could not be turned into a valid domain object.",
            {"errors": _validation_errors(exc.errors())},
        )

    @app.exception_handler(EntityNotFoundError)
    async def _handle_not_found(_: Request, exc: EntityNotFoundError) -> JSONResponse:
        return _json(404, "NOT_FOUND", str(exc))

    @app.exception_handler(UnsupportedFeatureError)
    async def _handle_unsupported(
        _: Request, exc: UnsupportedFeatureError
    ) -> JSONResponse:
        return _json(422, "UNSUPPORTED_FEATURE", str(exc))

    @app.exception_handler(AdapterValidationError)
    async def _handle_adapter_validation(
        _: Request, exc: AdapterValidationError
    ) -> JSONResponse:
        return _json(422, "ADAPTER_VALIDATION_ERROR", str(exc))

    @app.exception_handler(AdapterComputationError)
    async def _handle_adapter_computation(
        _: Request, exc: AdapterComputationError
    ) -> JSONResponse:
        # The backend's raw failure message may carry internal detail, so it is
        # logged server-side only; the client sees a fixed, generic message.
        _logger.exception("Adapter computation failed: %s", exc)
        return _json(502, "ADAPTER_COMPUTATION_FAILED", _ADAPTER_COMPUTATION_MESSAGE)

    @app.exception_handler(AdapterError)
    async def _handle_adapter_error(_: Request, exc: AdapterError) -> JSONResponse:
        # Any adapter-boundary error not matched more specifically above; treat
        # it like a computation failure and never surface the raw message.
        _logger.exception("Adapter error: %s", exc)
        return _json(502, "ADAPTER_ERROR", _ADAPTER_GENERIC_MESSAGE)

    @app.exception_handler(AgentNotConfiguredError)
    async def _handle_agent_not_configured(
        _: Request, exc: AgentNotConfiguredError
    ) -> JSONResponse:
        return _json(503, "AGENT_NOT_CONFIGURED", str(exc))

    @app.exception_handler(StaleAgentProposalError)
    async def _handle_stale_proposal(
        _: Request, exc: StaleAgentProposalError
    ) -> JSONResponse:
        return _json(409, "STALE_AGENT_PROPOSAL", str(exc))

    @app.exception_handler(InvalidAgentOutputError)
    async def _handle_invalid_agent_output(
        _: Request, exc: InvalidAgentOutputError
    ) -> JSONResponse:
        return _json(502, "AGENT_INVALID_OUTPUT", str(exc))

    @app.exception_handler(AgentModelError)
    async def _handle_agent_model_error(
        _: Request, exc: AgentModelError
    ) -> JSONResponse:
        # The upstream failure message is kept generic (it may name the model or
        # transport); the key is never present in these messages by construction.
        return _json(502, "AGENT_MODEL_ERROR", str(exc))

    @app.exception_handler(AgentActionRejectedError)
    async def _handle_agent_action_rejected(
        _: Request, exc: AgentActionRejectedError
    ) -> JSONResponse:
        return _json(409, "CONFLICT", str(exc))

    @app.exception_handler(StateTransitionError)
    async def _handle_state_transition(
        _: Request, exc: StateTransitionError
    ) -> JSONResponse:
        return _json(409, "CONFLICT", str(exc))

    @app.exception_handler(PersistenceError)
    async def _handle_persistence(_: Request, exc: PersistenceError) -> JSONResponse:
        return _json(
            409,
            "CONFLICT",
            "The operation conflicts with the current stored state.",
        )

    @app.exception_handler(ServiceError)
    async def _handle_service(_: Request, exc: ServiceError) -> JSONResponse:
        # EntityNotFoundError is handled above; everything else is a state or
        # duplicate-action conflict.
        return _json(409, "CONFLICT", str(exc))

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Never leak a traceback or backend-internal message to the client.
        return _json(
            500, "INTERNAL_ERROR", "An unexpected internal error occurred."
        )


def _validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce pydantic/FastAPI error entries to a compact, JSON-safe list."""
    compact: list[dict[str, Any]] = []
    for error in errors:
        compact.append(
            {
                "location": [str(part) for part in error.get("loc", ())],
                "message": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return compact


__all__ = ["ApiError", "register_exception_handlers"]

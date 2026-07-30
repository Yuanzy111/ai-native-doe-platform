"""The agent model boundary (§二): a narrow Protocol plus one OpenAI-compatible impl.

Everything above this layer (the :class:`~backend.agent.service.AgentService`)
depends only on the :class:`AgentModel` Protocol, so tests inject a deterministic
fake and never touch the network or spend tokens. The single production
implementation, :class:`OpenAICompatibleAgentModel`, talks to any
OpenAI-compatible chat-completions endpoint and is *lazily* wired: ``openai`` is
imported inside the methods so the app boots without the optional ``agent``
extra, and :func:`build_agent_model_from_env` returns ``None`` when the
``AGENT_*`` env is absent (the API still serves; only agent routes 503).

The API key is read from the environment and handed to the SDK; it is never
logged, echoed into an error message, or returned to a caller.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from typing import Any

from backend.agent.errors import AgentDependencyMissingError, AgentModelError

# A real chat completion may take tens of seconds; cap it so a hung provider
# cannot block a request thread indefinitely. The SDK retries a *bounded* number
# of times on transient failures (connection error, 408/409/429, >=500) — one
# extra attempt, not an unbounded loop — before surfacing a stable AgentModelError.
_REQUEST_TIMEOUT_SECONDS = 45.0
_MAX_RETRIES = 1


@runtime_checkable
class AgentModel(Protocol):
    """The one operation the service needs: turn a prompt + history into JSON text.

    ``messages`` is the running conversation as ``{"role", "content"}`` dicts
    (roles ``"user"``/``"assistant"``, plus any injected read-only context). The
    return value is the model's raw text, expected to be a JSON ``AgentTurn`` —
    parsing/validation is the caller's job, not this layer's.
    """

    def generate(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        ...


class OpenAICompatibleAgentModel:
    """An :class:`AgentModel` backed by any OpenAI-compatible chat endpoint.

    Configured entirely from ``base_url``/``api_key``/``model``. Construction
    stores config only — the ``openai`` SDK is neither imported nor a client
    built until the first :meth:`generate`, so an app whose ``AGENT_*`` env is
    set but whose optional ``agent`` extra is not installed still boots; the
    missing dependency surfaces as :class:`AgentDependencyMissingError` only when
    a message is actually sent. JSON output is requested via ``response_format``
    and the response text is returned verbatim.
    """

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._client: Any | None = None

    def _get_client(self) -> Any:
        """Lazily build and cache the OpenAI client on first use.

        Raises:
            AgentDependencyMissingError: If the optional ``openai`` package is
                not installed.
        """
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise AgentDependencyMissingError(
                    "The 'openai' package is required for the agent model; "
                    "install the optional 'agent' extra."
                ) from exc
            self._client = OpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                max_retries=_MAX_RETRIES,
            )
        return self._client

    def generate(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        client = self._get_client()
        from openai import OpenAIError

        payload = [{"role": "system", "content": system_prompt}, *messages]
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=payload,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
            )
        except OpenAIError as exc:
            # Surface a stable, key-free message; never include the provider's
            # raw payload which could echo credentials or internal detail.
            raise AgentModelError(f"The agent model call failed: {type(exc).__name__}.") from exc

        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise AgentModelError("The agent model returned an empty response.")
        return content


def build_agent_model_from_env() -> AgentModel | None:
    """Build the production model from ``AGENT_*`` env, or ``None`` if unset.

    Requires ``AGENT_BASE_URL``, ``AGENT_API_KEY``, and ``AGENT_MODEL`` to all be
    present and non-empty; a partial configuration returns ``None`` so the app
    boots and agent routes report ``AGENT_NOT_CONFIGURED`` rather than crashing.
    """
    base_url = os.environ.get("AGENT_BASE_URL", "").strip()
    api_key = os.environ.get("AGENT_API_KEY", "").strip()
    model = os.environ.get("AGENT_MODEL", "").strip()
    if not (base_url and api_key and model):
        return None
    return OpenAICompatibleAgentModel(base_url=base_url, api_key=api_key, model=model)


__all__ = ["AgentModel", "OpenAICompatibleAgentModel", "build_agent_model_from_env"]

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

from backend.agent.errors import AgentModelError


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

    Configured entirely from ``base_url``/``api_key``/``model``; the SDK is
    imported lazily so importing this module never requires the optional
    ``openai`` dependency. JSON output is requested via ``response_format`` and
    the response text is returned verbatim.
    """

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise AgentModelError(
                "The 'openai' package is required for the agent model; install "
                "the optional 'agent' extra."
            ) from exc
        self._model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def generate(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        from openai import OpenAIError

        payload = [{"role": "system", "content": system_prompt}, *messages]
        try:
            response = self._client.chat.completions.create(
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

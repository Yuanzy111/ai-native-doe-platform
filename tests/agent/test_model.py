"""Unit tests for the optional-SDK behavior of the agent model (§8).

The production :class:`OpenAICompatibleAgentModel` must construct without the
optional ``openai`` package present, so an app whose ``AGENT_*`` env is set but
whose ``agent`` extra is not installed still boots. The missing dependency
surfaces as :class:`AgentDependencyMissingError` only on the first
:meth:`generate`, and the raised message never carries the API key.
"""

from __future__ import annotations

import sys
import types

import pytest

from backend.agent.errors import AgentDependencyMissingError
from backend.agent.model import OpenAICompatibleAgentModel, build_agent_model_from_env

_SECRET = "sk-super-secret-key"


def test_construction_does_not_import_openai(monkeypatch):
    # Even with ``openai`` unimportable, constructing the model must not raise.
    monkeypatch.setitem(sys.modules, "openai", None)
    model = OpenAICompatibleAgentModel(
        base_url="https://example.test/v1", api_key=_SECRET, model="gpt-x"
    )
    assert model is not None


def test_generate_without_sdk_raises_dependency_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    model = OpenAICompatibleAgentModel(
        base_url="https://example.test/v1", api_key=_SECRET, model="gpt-x"
    )
    with pytest.raises(AgentDependencyMissingError) as excinfo:
        model.generate("system", [{"role": "user", "content": "hi"}])
    # The key is never leaked in the error message.
    assert _SECRET not in str(excinfo.value)


def test_client_built_with_bounded_timeout_and_retries(monkeypatch):
    # The lazily built client must carry a finite timeout and a bounded retry
    # count so a hung or flaky provider cannot block indefinitely or loop.
    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_module = types.ModuleType("openai")
    fake_module.OpenAI = _FakeOpenAI
    fake_module.OpenAIError = Exception
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    model = OpenAICompatibleAgentModel(
        base_url="https://example.test/v1", api_key=_SECRET, model="gpt-x"
    )
    model._get_client()

    assert captured["timeout"] == 45.0
    assert captured["max_retries"] == 1
    # The key is passed to the SDK but is not otherwise exposed.
    assert captured["api_key"] == _SECRET


def test_build_from_env_returns_none_when_unconfigured(monkeypatch):
    for var in ("AGENT_BASE_URL", "AGENT_API_KEY", "AGENT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert build_agent_model_from_env() is None


def test_build_from_env_constructs_without_sdk(monkeypatch):
    # A fully configured env builds the model even when the SDK is absent.
    monkeypatch.setitem(sys.modules, "openai", None)
    monkeypatch.setenv("AGENT_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AGENT_API_KEY", _SECRET)
    monkeypatch.setenv("AGENT_MODEL", "gpt-x")
    model = build_agent_model_from_env()
    assert isinstance(model, OpenAICompatibleAgentModel)

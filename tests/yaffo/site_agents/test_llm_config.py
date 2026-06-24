"""Per-provider key resolution + selected-model wiring that the generation routes and
the agent factory depend on. The selected model (DB-backed) and the keychain are
stubbed so this stays a pure unit test; keys are read from env vars, which win over
the keychain anyway."""
import pytest

from yaffo.site_agents import llm_config

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    # Never touch the real keyring (would prompt / vary by machine); env vars still win.
    monkeypatch.setattr(llm_config, "_read_keychain_key", lambda keychain_key: None)


def _select(monkeypatch, model_id):
    monkeypatch.setattr(llm_config, "get_model", lambda session=None: model_id)


def test_status_lists_every_provider_and_the_model_list(monkeypatch):
    _select(monkeypatch, "deepseek-chat")
    status = llm_config.status()
    provider_ids = {p["id"] for p in status["providers"]}
    assert {"anthropic", "openai", "grok", "gemini", "kimi", "deepseek"} <= provider_ids
    # Each model entry carries the provider that the grouped dropdown groups by.
    assert all("provider" in m for m in status["models"])
    assert status["selected_provider"] == "deepseek"


def test_selected_provider_follows_the_model(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    _select(monkeypatch, "deepseek-chat")
    assert llm_config.selected_model_provider() == "deepseek"
    assert llm_config.selected_provider_label() == "DeepSeek"
    assert llm_config.selected_key_missing() is True  # no key for that provider


def test_env_var_key_satisfies_the_gate(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    _select(monkeypatch, "deepseek-chat")
    assert llm_config.get_api_key("deepseek") == "sk-test"
    assert llm_config.selected_key_missing() is False
    # A different provider's key is independent (and unset here).
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm_config.get_api_key("openai") is None


def test_unknown_provider_key_is_none():
    assert llm_config.get_api_key("nope") is None

import pytest

from yaffo.db import db
from yaffo.db.models import ApplicationSettings
from yaffo.site_agents import llm_config
from yaffo.site_agents.assistant import settings as assistant_settings

pytestmark = pytest.mark.unit


def test_defaults_on_with_the_cheapest_model_of_the_generation_provider(app):
    assert assistant_settings.is_enabled() is True
    # The default AI Generation model is Anthropic's; its cheapest model is Haiku.
    assert assistant_settings.resolve_model() == "claude-haiku-4-5-20251001"


def test_default_follows_the_generation_provider(app):
    llm_config.set_model("gpt-5.1")
    assert assistant_settings.resolve_model() == "gpt-5.1"
    assert assistant_settings.model_provider_id() == "openai"


def test_saved_override_does_not_change_automatic_model(app):
    db.session.add(ApplicationSettings(
        name="assistant_model", type="string", value="gpt-5.1"))
    db.session.commit()
    assert assistant_settings.resolve_model() == "claude-haiku-4-5-20251001"
    assert assistant_settings.model_provider_id() == "anthropic"


def test_toggle_and_api_key_follow_the_assistant_model(app, monkeypatch):
    assistant_settings.set_enabled(False)
    assert assistant_settings.is_enabled(db.session) is False
    monkeypatch.setattr(llm_config, "get_api_key", lambda provider: f"key-{provider}")
    llm_config.set_model("gpt-5.1")
    assert assistant_settings.api_key() == "key-openai"
    assert assistant_settings.provider_label() == "OpenAI"

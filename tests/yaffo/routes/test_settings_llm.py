"""Route tests for the AI Generation model selector + single per-provider key box.

Changing the model persists it, fires a confirmation toast, and swaps the single key
box to that model's provider — without re-rendering the <select> (so its state is kept).
"""
import json

import pytest
from bs4 import BeautifulSoup

from yaffo.site_agents import llm_config
from yaffo.site_agents import model_catalog
from yaffo.site_agents.model_clients import providers
from yaffo.db import db

pytestmark = pytest.mark.unit


def _notification(resp):
    return json.loads(resp.headers["HX-Trigger"])["showNotification"]


def test_model_change_persists_toasts_and_swaps_key_box(app, client):
    resp = client.post("/settings/llm/model", data={"model": "claude-opus-4-8"})

    assert resp.status_code == 200
    assert _notification(resp)["type"] == "success"
    body = resp.get_data(as_text=True)
    assert 'id="llm-api-key"' in body            # the single key box was returned
    assert "Anthropic API key" in body           # for the selected model's provider
    assert 'id="llm-active-model" hx-swap-oob="outerHTML"' in body
    assert "Anthropic · Claude Opus 4.8" in body
    with app.app_context():
        assert llm_config.get_model() == "claude-opus-4-8"


def test_model_change_to_other_vendor_shows_that_vendors_key_box(app, client):
    resp = client.post("/settings/llm/model", data={"model": "deepseek-chat"})
    assert resp.status_code == 200
    assert "DeepSeek API key" in resp.get_data(as_text=True)
    with app.app_context():
        assert llm_config.get_model() == "deepseek-chat"


def test_unknown_model_is_rejected(app, client):
    with app.app_context():
        before = llm_config.get_model()
    resp = client.post("/settings/llm/model", data={"model": "not-a-model"})
    assert resp.status_code == 400
    with app.app_context():
        assert llm_config.get_model() == before  # invalid id rejected by set_model


def test_localized_model_labels_do_not_mutate_shared_status(app, client):
    client.post("/settings/locale", data={"locale": "de"})
    german = client.get("/settings/llm/models?provider=anthropic").get_data(as_text=True)

    assert "Claude Sonnet 4.6 — ausgewogen" in german
    with app.app_context():
        sonnet = next(
            model
            for model in llm_config.status()["models"]
            if model["id"] == "claude-sonnet-4-6"
        )
    assert sonnet["label"] == "Claude Sonnet 4.6 — balanced"


def test_model_lookup_returns_a_searchable_list_and_saves_a_new_selection(client, monkeypatch):
    monkeypatch.setattr(llm_config, "get_api_key", lambda provider_id: "key" if provider_id == "openai" else None)
    monkeypatch.setattr(model_catalog, "discover", lambda provider, key: [
        providers.Model("gpt-new-example", "New OpenAI Model", "openai", None, 32768)])
    response = client.get("/settings/llm/models?provider=openai")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'value="openai:gpt-new-example"' in body
    assert "New OpenAI Model" in body and "data-search-disabled" not in body
    soup = BeautifulSoup(body, "html.parser")
    assert soup.select_one(".help-tip")['data-tooltip'] == (
        "Models are looked up using your saved provider keys. The last saved list is available offline.")
    assert not any("Models are looked up" in paragraph.get_text() for paragraph in soup.select("p"))
    assert 'hx-trigger="llm-models-refresh from:body"' in body
    assert "models found" not in body and "Refresh models" not in body
    chosen = client.post("/settings/llm/model", data={"model": "openai:gpt-new-example"})
    assert chosen.status_code == 200 and "OpenAI API key" in chosen.get_data(as_text=True)
    assert llm_config.get_model(db.session) == "openai:gpt-new-example"


def test_lookup_failure_keeps_the_selected_model_and_saved_options(client, monkeypatch):
    monkeypatch.setattr(llm_config, "get_api_key", lambda provider_id: "key" if provider_id == "openai" else None)
    monkeypatch.setattr(model_catalog, "discover", lambda provider, key: [
        providers.Model("gpt-new-example", "New OpenAI Model", "openai", None)])
    client.get("/settings/llm/models?provider=openai")
    client.post("/settings/llm/model", data={"model": "openai:gpt-new-example"})
    def unavailable(provider, key):
        raise model_catalog.DiscoveryError("unavailable")
    monkeypatch.setattr(model_catalog, "discover", unavailable)
    body = client.get("/settings/llm/models?provider=openai").get_data(as_text=True)
    assert 'value="openai:gpt-new-example" selected' in body
    assert "The provider could not be reached" in body


def test_provider_starts_empty_and_no_models_are_looked_up_on_page_load(client, monkeypatch):
    calls = []
    monkeypatch.setattr(model_catalog, "discover", lambda provider, key: calls.append(provider.id))
    soup = BeautifulSoup(client.get("/settings").data, "html.parser")
    provider = soup.select_one("#llm-provider")
    assert provider.select_one("option[selected]")["value"] == ""
    assert provider["hx-target"] == "#llm-provider-models"
    assert soup.select_one("#llm-model") is None
    assert "Claude Sonnet 4.6" in soup.select_one("#llm-active-model").get_text()
    assert "load" not in soup.select_one("#llm-provider-models")["hx-trigger"]
    assert calls == []
    assert client.get("/settings/llm/models").status_code == 200 and calls == []


def test_provider_selection_looks_up_only_that_providers_models(client, monkeypatch):
    calls = []
    monkeypatch.setattr(llm_config, "get_api_key", lambda provider_id: "key")
    def discover(provider, key):
        calls.append(provider.id)
        return [providers.Model("new-model", "New Model", provider.id, None)]
    monkeypatch.setattr(model_catalog, "discover", discover)
    body = client.get("/settings/llm/models?provider=deepseek").get_data(as_text=True)
    assert calls == ["deepseek"]
    assert 'value="deepseek:new-model"' in body and 'value="claude-sonnet-4-6"' not in body
    assert "DeepSeek API key" in body and "models found" not in body

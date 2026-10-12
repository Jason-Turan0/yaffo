"""Live listings must be bounded, survive restarts, and route a new model to its provider."""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from io import BytesIO

import pytest
import requests

from yaffo.db import db
from yaffo.app import create_app
from yaffo.db.repositories import llm_model_repository as repository
from yaffo.site_agents import llm_config, model_catalog as catalog
from yaffo.site_agents.model_clients import providers
from yaffo.site_agents.model_clients.factory import create_model_client

pytestmark = pytest.mark.unit


@pytest.fixture
def app(tmp_path):
    application = create_app(db_path=tmp_path / "test.db", config={"TESTING": True})
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


class FakeHTTP:
    def __init__(self, payloads, status=200):
        self.payloads = iter(payloads)
        self.status = status
        self.calls = []
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        response = requests.Response()
        response.status_code = self.status
        response.raw = BytesIO(json.dumps(next(self.payloads)).encode())
        return response


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(providers, "_discovered_models", {})


def _http(monkeypatch, *payloads, status=200):
    http = FakeHTTP(payloads, status)
    monkeypatch.setattr(catalog.requests, "Session", lambda: http)
    return http


def test_openai_looks_up_new_chat_models_and_excludes_other_api_types(monkeypatch):
    http = _http(monkeypatch, {"data": [{"id": name} for name in (
        "gpt-6-example", "gpt-4o-mini", "text-embedding-3-small", "gpt-image-1", "gpt-5.1-codex",
        "gpt-4o-realtime-preview", "gpt-5-pro")]})
    models = catalog.discover(providers.get_provider("openai"), "private-key")
    assert {model.id for model in models} == {"gpt-6-example", "gpt-4o-mini"}
    endpoint, request = http.calls[0]
    assert endpoint == "https://api.openai.com/v1/models"
    assert request["headers"]["Authorization"] == "Bearer private-key"
    assert request["allow_redirects"] is False and http.trust_env is False
    assert next(model for model in models if model.id == "gpt-4o-mini").max_output_tokens == 16384


def test_gemini_pagination_preserves_labels_and_limits(monkeypatch):
    http = _http(monkeypatch,
                 {"models": [{"name": "models/gemini-example", "displayName": "Example Gemini",
                              "supportedGenerationMethods": ["generateContent"], "outputTokenLimit": 8192},
                             {"name": "models/gemini-embedding", "supportedGenerationMethods": ["embedContent"]}],
                  "nextPageToken": "page-two"},
                 {"models": [{"name": "models/gemini-example-image", "supportedGenerationMethods": ["generateContent"]}]})
    models = catalog.discover(providers.get_provider("gemini"), "private-key")
    assert [(model.id, model.label, model.max_output_tokens) for model in models] == [
        ("gemini-example", "Example Gemini", 8192)]
    assert http.calls[1][1]["params"]["pageToken"] == "page-two"
    assert http.calls[0][1]["headers"]["x-goog-api-key"] == "private-key"
    assert all("private-key" not in endpoint for endpoint, _ in http.calls)


def test_anthropic_pagination_and_retired_models(monkeypatch):
    _http(monkeypatch, {"data": [{"id": "claude-example", "display_name": "New Claude", "max_tokens": 8192}],
                        "has_more": True, "last_id": "claude-example"},
                      {"data": [{"id": "claude-old", "lifecycle": "retired"}], "has_more": False})
    found = catalog.discover(providers.get_provider("anthropic"), "key")
    assert [(model.id, model.label) for model in found] == [("claude-example", "New Claude")]


@pytest.mark.parametrize("provider_id", ["kimi", "deepseek"])
def test_other_compatible_providers_use_their_own_model_endpoint(monkeypatch, provider_id):
    http = _http(monkeypatch, {"data": [{"id": "new-model", "name": "New Model", "max_output_tokens": 8192,
                                       "output_modalities": ["text"]}]})
    found = catalog.discover(providers.get_provider(provider_id), "key")
    assert found[0].id == "new-model" and found[0].provider_id == provider_id
    assert http.calls[0][0] == providers.get_provider(provider_id).base_url + "/models"


def test_xai_uses_the_language_model_catalog_and_its_pricing_units(monkeypatch):
    http = _http(monkeypatch, {"models": [{"id": "grok-example", "prompt_text_token_price": 12500,
                                         "completion_text_token_price": 25000, "cached_prompt_text_token_price": 2000}]})
    model = catalog.discover(providers.get_provider("grok"), "key")[0]
    assert http.calls[0][0] == "https://api.x.ai/v1/language-models"
    assert model.pricing == providers.ModelPricing(1.25, 2.5, .2)


def test_saved_discovery_routes_new_models_after_a_worker_restart(app, monkeypatch, tmp_path):
    key = lambda provider_id: "key" if provider_id == "gemini" else None
    _http(monkeypatch, {"models": [{"name": "models/gemini-example", "displayName": "Example Gemini",
                                    "supportedGenerationMethods": ["generateContent"], "outputTokenLimit": 8192}]})
    assert catalog.refresh(db.session, key)[0].status == "ok"
    assert llm_config.set_model("gemini:gemini-example")
    providers.register_discovered_models([])
    assert llm_config.get_model(db.session) == "gemini:gemini-example"
    assert providers.get_model("gemini:gemini-example").label == "Example Gemini"
    model = create_model_client(model=llm_config.get_model(), system_prompt="Test", providers=[], api_key="key",
                                log_dir=tmp_path / "logs")
    assert model.provider_id == "gemini" and model._build_params()["model"] == "gemini-example"
    assert model.max_tokens == 8192
    assert model._estimate_cost(type("Usage", (), {"input_tokens": 5})()) is None
    assert "key" not in json.dumps(repository.catalogs(db.session))


def test_failure_keeps_last_good_list_and_selected_model(app, monkeypatch):
    model = providers.Model("gpt-example", "Example", "openai", None)
    repository.save(db.session, "openai", {"models": [asdict(model)], "refreshed_at": None})
    db.session.commit()
    assert llm_config.set_model("openai:gpt-example")
    _http(monkeypatch, {"error": "private-key"}, status=401)
    result = catalog.refresh(db.session, lambda provider_id: "private-key" if provider_id == "openai" else None,
                             force=True)
    assert result[0].status == "auth"
    assert llm_config.get_model() == "openai:gpt-example"
    assert any(model.id == "gpt-example" for model in catalog.cached_models(db.session))
    assert "private-key" not in repr(result)


def test_fresh_catalog_and_providers_without_keys_make_no_requests(app, monkeypatch):
    model = providers.Model("gpt-example", "Example", "openai", None)
    repository.save(db.session, "openai", {"models": [asdict(model)],
                                           "refreshed_at": datetime.now(timezone.utc).isoformat()})
    db.session.commit()
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected outbound request")
    monkeypatch.setattr(catalog.requests, "Session", forbidden)
    assert catalog.refresh(db.session, lambda provider_id: "key" if provider_id == "openai" else None) == []


def test_credentials_changed_during_discovery_cannot_overwrite_the_catalog(app, monkeypatch):
    keys = {"openai": "old"}
    def discovered(provider, api_key):
        keys[provider.id] = "new"
        return [providers.Model("gpt-example", "Example", "openai", None)]
    monkeypatch.setattr(catalog, "discover", discovered)
    assert catalog.refresh(db.session, keys.get)[0].status == "key_changed"
    assert repository.catalogs(db.session) == {}


def test_repeating_pagination_and_oversized_responses_fail_boundedly(monkeypatch):
    payload = {"data": [{"id": "claude-example"}], "has_more": True, "last_id": "same"}
    _http(monkeypatch, payload, payload)
    with pytest.raises(catalog.DiscoveryError, match="invalid_response"):
        catalog.discover(providers.get_provider("anthropic"), "key")
    _http(monkeypatch, {"data": [{"id": "gpt-example"}], "padding": "x" * 1000})
    monkeypatch.setattr(catalog, "MAX_RESPONSE_BYTES", 100)
    with pytest.raises(catalog.DiscoveryError, match="too_large"):
        catalog.discover(providers.get_provider("openai"), "key")

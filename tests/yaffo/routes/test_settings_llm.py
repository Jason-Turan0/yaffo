"""Route tests for the AI Generation model selector + single per-provider key box.

Changing the model persists it, fires a confirmation toast, and swaps the single key
box to that model's provider — without re-rendering the <select> (so its state is kept).
"""
import json

import pytest

from yaffo.site_agents import llm_config

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
    with app.app_context():
        assert llm_config.get_model() == "claude-opus-4-8"


def test_model_change_to_other_vendor_shows_that_vendors_key_box(app, client):
    resp = client.post("/settings/llm/model", data={"model": "deepseek-chat"})
    assert resp.status_code == 200
    assert "DeepSeek API key" in resp.get_data(as_text=True)
    with app.app_context():
        assert llm_config.get_model() == "deepseek-chat"


def test_unknown_model_is_ignored(app, client):
    with app.app_context():
        before = llm_config.get_model()
    resp = client.post("/settings/llm/model", data={"model": "not-a-model"})
    assert resp.status_code == 200
    with app.app_context():
        assert llm_config.get_model() == before  # invalid id rejected by set_model

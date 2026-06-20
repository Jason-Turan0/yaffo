"""Route test for the AI Generation model selector.

Changing the model must persist *only* the model and give a toast — without
re-rendering the section, so an in-progress (unsaved) API key in the key field isn't
wiped. (The key itself lives in the keychain and set_model never touches it.)
"""
import json

import pytest

from yaffo.site_agents import llm_config

pytestmark = pytest.mark.unit


def _notification(resp):
    return json.loads(resp.headers["HX-Trigger"])["showNotification"]


def test_model_change_persists_and_toasts_without_rerender(app, client):
    resp = client.post("/settings/llm/model", data={"model": "claude-opus-4-8"})

    assert resp.status_code == 204            # no section fragment -> key field untouched
    assert resp.get_data() == b""
    assert _notification(resp)["type"] == "success"
    with app.app_context():
        assert llm_config.get_model() == "claude-opus-4-8"


def test_unknown_model_is_ignored(app, client):
    with app.app_context():
        before = llm_config.get_model()
    resp = client.post("/settings/llm/model", data={"model": "not-a-model"})
    assert resp.status_code == 204
    with app.app_context():
        assert llm_config.get_model() == before  # invalid id rejected by set_model

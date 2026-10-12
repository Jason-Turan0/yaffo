"""Domain approval is a server decision tied to one pending card and conversation."""
import pytest
from bs4 import BeautifulSoup

from yaffo.db import db
from yaffo.db.repositories import assistant_repository as conversations
from yaffo.db.repositories import assistant_web_repository as permissions
from yaffo.site_agents.assistant import settings

pytestmark = pytest.mark.unit


@pytest.fixture
def web(client, monkeypatch):
    runs = []
    monkeypatch.setattr("yaffo.routes.assistant.assistant_run_task", runs.append)
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda *args, **kwargs: "key")
    settings.set_web_enabled(True)
    conversation = conversations.create_conversation(db.session, "Read these docs")
    permission = permissions.ask(db.session, conversation.id, {
        "example.org": ["https://docs.example.org/api"], "example.net": ["https://example.net/api"]})
    return conversation.id, permission.id, runs


def _url(conversation_id, permission_id):
    return f"/assistant/conversations/{conversation_id}/web-permissions/{permission_id}"


def test_approve_selected_domains_and_continue_the_conversation(client, web):
    conversation_id, permission_id, runs = web
    body = client.get(f"/assistant/conversations/{conversation_id}").get_json()
    card = body["messages"][0]
    assert card["type"] == "web_permission" and card["payload"]["id"] == permission_id
    response = client.post(_url(conversation_id, permission_id), json={
        "decision": "allow", "domains": ["example.org"]})
    assert response.status_code == 202
    assert response.get_json()["conversation"]["status"] == "RUNNING"
    assert runs == [conversation_id]
    assert permissions.domain_access(db.session, conversation_id) == ({"example.org"}, {"example.net"})
    user = conversations.list_events(db.session, conversation_id)[-1]
    assert user.kind == "user" and "example.org" in user.content and "example.net" in user.content
    conversations.set_status(db.session, conversation_id, "IDLE")
    again = client.post(_url(conversation_id, permission_id), json={"decision": "allow"})
    assert again.status_code == 409 and again.get_json()["code"] == "permission_not_pending"
    assert runs == [conversation_id]


def test_deny_records_a_denial_and_continues_without_granting_access(client, web):
    conversation_id, permission_id, runs = web
    response = client.post(_url(conversation_id, permission_id), json={"decision": "deny"})
    assert response.status_code == 202 and runs == [conversation_id]
    assert permissions.domain_access(db.session, conversation_id) == (set(), {"example.org", "example.net"})


def test_another_conversation_cannot_decide_the_card(client, web):
    conversation_id, permission_id, runs = web
    other = conversations.create_conversation(db.session, "Another conversation")
    response = client.post(_url(other.id, permission_id), json={"decision": "allow"})
    assert response.status_code == 409
    assert permissions.domain_access(db.session, conversation_id) == (set(), set())
    assert permissions.domain_access(db.session, other.id) == (set(), set())
    assert runs == []


@pytest.mark.parametrize("body", [{"decision": "allow", "domains": ["evil.example"]},
                                  {"decision": "allow", "domains": "example.org"},
                                  {"decision": "allow", "domains": [123]},
                                  {"decision": []}, {}])
def test_invalid_decisions_cannot_widen_the_requested_access(client, web, body):
    conversation_id, permission_id, runs = web
    assert client.post(_url(conversation_id, permission_id), json=body).status_code == 400
    assert permissions.domain_access(db.session, conversation_id) == (set(), set())
    assert permissions.permissions(db.session, conversation_id)[0].status == "pending"
    assert runs == []


def test_decision_requires_web_enabled_and_a_finished_turn(client, web):
    conversation_id, permission_id, runs = web
    conversations.start_run(db.session, conversation_id, "gpt-5.1")
    assert client.post(_url(conversation_id, permission_id), json={"decision": "allow"}).status_code == 409
    conversations.set_status(db.session, conversation_id, "IDLE")
    settings.set_web_enabled(False)
    response = client.post(_url(conversation_id, permission_id), json={"decision": "allow"})
    assert response.status_code == 409 and response.get_json()["code"] == "web_disabled"
    assert permissions.domain_access(db.session, conversation_id) == (set(), set()) and runs == []


def test_web_reads_start_on_and_the_switch_controls_them(client):
    assert settings.web_enabled() is True


def test_setting_appears_under_what_it_can_look_at(client, web):
    soup = BeautifulSoup(client.get("/settings", headers={"Accept-Language": "en"}).data, "html.parser")
    switch = soup.select_one("#assistant-web-enabled")
    assert switch.has_attr("checked")
    assert switch.find_parent("label").get_text(strip=True) == "Read public websites"
    assert switch.find_parent("fieldset").legend.get_text(strip=True) == "What it can look at"
    assert "Web page reads" not in [legend.get_text(strip=True) for legend in soup.select("#assistant-section legend")]
    assert client.post("/settings/assistant/web", data={}).status_code == 200
    assert settings.web_enabled() is False
    assert client.post("/settings/assistant/web", data={"enabled": "on"}).status_code == 200
    assert settings.web_enabled() is True

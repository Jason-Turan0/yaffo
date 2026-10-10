"""The adapter web layer: grants, follow-up URLs, hosts, credentials, and downloads,
against the fake Microsoft Graph."""
from pathlib import Path

import pytest
import requests

from yaffo.sync import connections
from yaffo.sync.oauth import TokenSource
from yaffo.sync.providers import ONEDRIVE
from yaffo.sync.schemas import CONNECTION_NEEDS_SIGN_IN, GRANT_ENDPOINT, GRANT_FOLLOWUP, WebGrant
from yaffo.sync.web import CONNECTION_TOKEN, DownloadError, WebClient, url_hash, url_template

GRAPH = "https://graph.microsoft.com/v1.0"
CODE = "code-hash-1"


def grant(method, pattern, kind=GRANT_ENDPOINT, risk="read", code=CODE):
    return WebGrant(method=method, url_pattern=pattern, grant_kind=kind, followup_of=None, risk=risk,
                    granted_code_hash=code, granted_at="2026-10-10T00:00:00Z")


GRANTS = [
    grant("GET", f"{GRAPH}/me"),
    grant("GET", f"{GRAPH}/me/drive/items/{{id}}/delta"),
    grant("GET", None, kind=GRANT_FOLLOWUP),
    grant("POST", f"{GRAPH}/me/drive/items/{{id}}/copy", risk="write"),
]


@pytest.fixture
def connection_key(session, fake_onedrive):
    return connections.upsert_after_sign_in(session, "onedrive", "fake-user-1", "jason@example.com",
                                            ["Files.Read", "User.Read"], fake_onedrive.grant_token())


@pytest.fixture
def client(session, connection_key):
    def make(grants=GRANTS, read_only=False, vouched=()):
        tokens = TokenSource(session.get_bind(), connection_key, ONEDRIVE)
        return WebClient(provider=ONEDRIVE, grants=list(grants), code_hash=CODE, tokens=tokens,
                         read_only=read_only, vouched_hashes=set(vouched), http=requests.Session())
    return make


def test_url_template_encodes_values():
    assert url_template(GRAPH + "/items/{id}/x", id="a/b c") == GRAPH + "/items/a%2Fb%20c/x"
    assert url_template(GRAPH + "/root:/{path}:", path="Pictures/For Mom") == GRAPH + "/root:/Pictures/For%20Mom:"
    with pytest.raises(ValueError):
        url_template(GRAPH + "/items/{id}")
    with pytest.raises(ValueError):
        url_template(GRAPH + "/items/{id}", id="1", extra="2")


def test_granted_endpoint_runs_with_credentials(client):
    result = client().web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me")
    assert result["status"] == 200 and result["json"]["userPrincipalName"] == "jason@example.com"


def test_ungranted_calls_and_foreign_hosts_are_denied_as_data(client, fake_onedrive):
    web = client()
    assert web.web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me/drive/root/children")["status"] == 0
    assert web.web_request(CONNECTION_TOKEN, "GET", "https://evil.example.com/steal")["status"] == 0
    assert web.web_request("another", "GET", f"{GRAPH}/me")["status"] == 0
    assert web.denied == [f"GET {GRAPH}/me/drive/root/children", "GET https://evil.example.com/steal"]
    assert fake_onedrive.state.counters.get("graph_calls", 0) == 0


def test_grants_for_other_code_dont_count(client):
    stale = [grant("GET", f"{GRAPH}/me", code="old-code")]
    assert client(grants=stale).web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me")["status"] == 0


def test_read_only_clients_only_make_read_calls(client):
    result = client(read_only=True).web_request(CONNECTION_TOKEN, "POST", f"{GRAPH}/me/drive/items/X/copy", json={})
    assert result["status"] == 0 and "only reads" in result["error"]


def test_follow_up_urls_must_come_from_a_granted_response(client, fake_onedrive):
    fake_onedrive.state.add_photos(25)
    web = client()
    forged = f"{GRAPH}/drives/D1/items/CAMROLL/delta?$skiptoken=0.10.99"
    assert web.web_request(CONNECTION_TOKEN, "GET", forged)["status"] == 0
    first = web.web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me/drive/items/CAMROLL/delta")
    next_link = first["json"]["@odata.nextLink"]
    assert web.web_request(CONNECTION_TOKEN, "GET", next_link)["status"] == 200
    # A later run (a new client) may fetch the stored cursor only if it was vouched.
    assert client().web_request(CONNECTION_TOKEN, "GET", next_link)["status"] == 0
    assert client(vouched=[url_hash(next_link)]).web_request(CONNECTION_TOKEN, "GET", next_link)["status"] == 200


def test_adapters_cannot_set_credentials_headers(client):
    result = client().web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me", headers={"Authorization": "Bearer x"})
    assert result["status"] == 0


def test_expired_token_is_refreshed_once_and_rotated(client, fake_onedrive, secrets_file):
    web = client()
    assert web.web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me")["status"] == 200
    fake_onedrive.state.access.clear()  # the provider forgets the access token
    assert web.web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me")["status"] == 200
    assert fake_onedrive.state.counters["refreshes"] == 1
    # The rotated refresh token was stored: a second refresh works too.
    fake_onedrive.state.access.clear()
    assert client().web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me")["status"] == 200
    assert fake_onedrive.state.counters["refreshes"] == 2


def test_revoked_account_is_marked_needs_sign_in(client, fake_onedrive, session, connection_key):
    fake_onedrive.state.revoked = True
    web = client()
    result = web.web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me")
    assert result["status"] in (0, 401) and web.auth_failed
    session.expire_all()
    assert connections.get(session, connection_key).status == CONNECTION_NEEDS_SIGN_IN


def test_mid_page_401_defers_the_refresh_to_the_boundary(client, fake_onedrive):
    web = client()
    web.web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me")
    fake_onedrive.state.access.clear()
    web.mid_page = True
    assert web.web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me")["status"] == 401
    assert web.needs_refresh and "refreshes" not in fake_onedrive.state.counters
    web.mid_page = False
    web.boundary()
    assert fake_onedrive.state.counters["refreshes"] == 1
    assert web.web_request(CONNECTION_TOKEN, "GET", f"{GRAPH}/me")["status"] == 200


def test_download_follows_the_redirect_without_sending_credentials(client, fake_onedrive, tmp_path):
    item_id = fake_onedrive.state.add_photos(1)[0]
    destination = tmp_path / "x.jpg"
    client().download(f"{GRAPH}/me/drive/items/{item_id}/content", None, destination)
    assert destination.read_bytes() == fake_onedrive.state.items[item_id].content
    assert fake_onedrive.state.counters.get("download_with_credentials", 0) == 0


def test_download_refuses_foreign_hosts_and_oversized_files(client, fake_onedrive, tmp_path):
    item_id = fake_onedrive.state.add_photos(1)[0]
    with pytest.raises(DownloadError):
        client().download("https://evil.example.com/x.jpg", None, tmp_path / "a.jpg")
    with pytest.raises(DownloadError):
        client().download(f"{GRAPH}/me/drive/items/{item_id}/content", None, tmp_path / "b.jpg", max_bytes=10)


def test_tokens_never_reach_the_database(session, connection_key, secrets_file):
    from yaffo.db.models import ApplicationSettings

    row = session.query(ApplicationSettings).filter_by(name=connection_key).one()
    assert "AT-" not in row.value and "RT-" not in row.value
    assert "RT-" in Path(secrets_file).read_text()


@pytest.mark.parametrize("url, allowed", [
    ("https://my.microsoftpersonalcontent.com/personal/abc/_layouts/15/download.aspx?x=1", True),
    ("https://public.bn.files.1drv.com/y4m", True),
    ("https://contoso-my.sharepoint.com/personal/x", True),
    ("https://microsoftpersonalcontent.com.evil.example/x", False),
    ("http://my.microsoftpersonalcontent.com/x", False),
])
def test_onedrive_download_hosts(url, allowed):
    from yaffo.sync.providers import allowed_url, is_base_url

    assert allowed_url(ONEDRIVE, url) is allowed
    assert not is_base_url(ONEDRIVE, url)  # credentials never go to a download host

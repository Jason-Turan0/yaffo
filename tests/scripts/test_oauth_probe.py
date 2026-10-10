import base64
import hashlib
import json
import threading
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import requests

from scripts import oauth_probe
from scripts.oauth_probe import PROVIDERS, ProbeError, build_authorize_url, make_pkce, parse_callback, run_probe


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.content = self.text.encode()
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttp:
    """Stands in for requests.Session: canned token and API answers, calls recorded."""

    def __init__(self, token_answers, check_answer=(200, {"value": []})):
        self.token_answers = list(token_answers)
        self.check_answer = check_answer
        self.posts = []
        self.gets = []

    def post(self, url, data, timeout):
        self.posts.append((url, data))
        return _Response(*self.token_answers.pop(0))

    def get(self, url, headers, timeout):
        self.gets.append((url, headers))
        return _Response(*self.check_answer)


def _browser_returning(params_for):
    """A fake browser: reads the authorize URL, then hits the return address."""
    seen = {}

    def open_browser(url):
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(query)
        callback = f"{query['redirect_uri']}?{urlencode(params_for(query))}"
        threading.Thread(target=requests.get, args=(callback,), kwargs={"timeout": 5}, daemon=True).start()

    return open_browser, seen


def _good_tokens():
    return [
        (200, {"access_token": "at-1", "refresh_token": "rt-1", "scope": "Files.ReadWrite"}),
        (200, {"access_token": "at-2"}),
    ]


def test_pkce_challenge_is_s256_of_verifier():
    pkce = make_pkce()
    assert 43 <= len(pkce.verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(pkce.verifier.encode()).digest()).rstrip(b"=").decode()
    assert pkce.challenge == expected


def test_authorize_url_carries_pkce_state_and_provider_params():
    pkce = make_pkce()
    url = build_authorize_url(PROVIDERS["google"], "cid", "http://127.0.0.1:5/oauth/callback", "st", pkce)
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query["code_challenge"] == pkce.challenge
    assert query["code_challenge_method"] == "S256"
    assert query["state"] == "st"
    assert query["access_type"] == "offline"
    assert "photoslibrary.appendonly" in query["scope"]


def test_parse_callback_rejects_wrong_state_and_reports_errors():
    assert parse_callback("code=abc&state=s1", "s1") == "abc"
    with pytest.raises(ProbeError, match="mismatched state"):
        parse_callback("code=abc&state=other", "s1")
    with pytest.raises(ProbeError, match="access_denied"):
        parse_callback("error=access_denied&state=s1", "s1")
    with pytest.raises(ProbeError, match="neither"):
        parse_callback("state=s1", "s1")


def test_probe_signs_in_checks_api_and_refreshes():
    open_browser, seen = _browser_returning(lambda q: {"code": "the-code", "state": q["state"]})
    http = _FakeHttp(_good_tokens())
    lines = []

    run_probe(PROVIDERS["microsoft"], "cid", None, open_browser=open_browser, http=http,
              report=lines.append, timeout_seconds=5)

    # Bare loopback root: Microsoft matches the path against the registered
    # http://localhost, so any path here is rejected as invalid_request.
    redirect = urlparse(seen["redirect_uri"])
    assert (redirect.scheme, redirect.hostname, redirect.path) == ("http", "localhost", "")
    assert redirect.port
    exchange, refresh = (data for _, data in http.posts)
    assert exchange["code"] == "the-code"
    assert exchange["redirect_uri"] == seen["redirect_uri"]
    assert hashlib.sha256(exchange["code_verifier"].encode()).digest() == base64.urlsafe_b64decode(
        seen["code_challenge"] + "=")
    assert "client_secret" not in exchange
    assert refresh == {"grant_type": "refresh_token", "client_id": "cid", "refresh_token": "rt-1"}
    assert http.gets[0][1] == {"Authorization": "Bearer at-1"}
    assert not any("at-1" in line or "rt-1" in line for line in lines)


def test_probe_sends_client_secret_when_given():
    open_browser, _ = _browser_returning(lambda q: {"code": "c", "state": q["state"]})
    http = _FakeHttp(_good_tokens())
    run_probe(PROVIDERS["google"], "cid", "shh", open_browser=open_browser, http=http,
              report=lambda _: None, timeout_seconds=5)
    assert all(data["client_secret"] == "shh" for _, data in http.posts)


def test_probe_fails_without_refresh_token():
    open_browser, _ = _browser_returning(lambda q: {"code": "c", "state": q["state"]})
    http = _FakeHttp([(200, {"access_token": "at-1"})])
    with pytest.raises(ProbeError, match="no refresh token"):
        run_probe(PROVIDERS["google"], "cid", "s", open_browser=open_browser, http=http,
                  report=lambda _: None, timeout_seconds=5)


def test_probe_reports_token_endpoint_refusal():
    open_browser, _ = _browser_returning(lambda q: {"code": "c", "state": q["state"]})
    http = _FakeHttp([(400, {"error": "invalid_client", "error_description": "bad id"})])
    with pytest.raises(ProbeError, match="invalid_client bad id"):
        run_probe(PROVIDERS["microsoft"], "cid", None, open_browser=open_browser, http=http,
                  report=lambda _: None, timeout_seconds=5)


def test_probe_reports_api_check_failure():
    open_browser, _ = _browser_returning(lambda q: {"code": "c", "state": q["state"]})
    http = _FakeHttp(_good_tokens(), check_answer=(403, {"error": "insufficient scopes"}))
    with pytest.raises(ProbeError, match="answered 403"):
        run_probe(PROVIDERS["google"], "cid", "s", open_browser=open_browser, http=http,
                  report=lambda _: None, timeout_seconds=5)


def test_probe_times_out_when_nobody_signs_in():
    with pytest.raises(ProbeError, match="no sign-in response"):
        run_probe(PROVIDERS["microsoft"], "cid", None, open_browser=lambda url: None,
                  http=_FakeHttp([]), report=lambda _: None, timeout_seconds=1.5)


def test_main_requires_a_client_id(monkeypatch, capsys):
    monkeypatch.delenv("YAFFO_OAUTH_MICROSOFT_CLIENT_ID", raising=False)
    with pytest.raises(SystemExit):
        oauth_probe.main(["microsoft"])
    assert "YAFFO_OAUTH_MICROSOFT_CLIENT_ID" in capsys.readouterr().err

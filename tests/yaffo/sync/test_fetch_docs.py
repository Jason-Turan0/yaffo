"""The service builder's documentation reads: registrable domains from the bundled
Public Suffix List, and fetch_docs authorizing per domain, never reaching a private
address, and turning pages into text the model can use."""
import json
from io import BytesIO

import pytest
import requests

from yaffo.sync import fetch_docs as docs
from yaffo.sync.doc_domains import registrable_domain
from yaffo.sync.fetch_docs import (
    STATUS_DENIED, STATUS_ERROR, STATUS_NEEDS_APPROVAL, STATUS_OK, condense_openapi, fetch_docs, html_to_markdown,
)

pytestmark = pytest.mark.unit

REAL_PUBLIC_ADDRESS = docs._public_address


@pytest.mark.parametrize(("host", "domain"), [
    ("www.flickr.com", "flickr.com"),
    ("api.flickr.com", "flickr.com"),
    ("alice.github.io", "alice.github.io"),          # github.io is a public suffix
    ("www.bbc.co.uk", "bbc.co.uk"),
    ("raw.githubusercontent.com", "raw.githubusercontent.com"),
    ("münchen.de", "xn--mnchen-3ya.de"),
    ("co.uk", None),
    ("github.io", None),
    ("127.0.0.1", None),
    ("localhost", None),
    ("", None),
])
def test_registrable_domains(host, domain):
    assert registrable_domain(host) == domain


def test_two_github_pages_sites_are_different_domains():
    assert registrable_domain("alice.github.io") != registrable_domain("bob.github.io")


class FakeHTTP:
    """Pages by URL: (status, content type, body, location)."""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        status, kind, body, location = self.pages[url]
        response = requests.Response()
        response.status_code = status
        response.raw = BytesIO(body.encode() if isinstance(body, str) else body)
        response.headers["content-type"] = kind
        if location:
            response.headers["location"] = location
        response.url = url
        response.encoding = "utf-8"
        return response


@pytest.fixture(autouse=True)
def public(monkeypatch):
    monkeypatch.setattr(docs, "_public_address", lambda host: True)


HTML = """<html><head><style>p{}</style><script>alert(1)</script></head><body><nav>Home Docs</nav>
<h1>Photosets</h1><p>Call <b>flickr.photosets.getList</b> to list sets.</p><footer>(c)</footer></body></html>"""


def test_an_allowed_domain_is_read_as_markdown():
    http = FakeHTTP({"https://www.flickr.com/services/api/": (200, "text/html", HTML, None)})

    batch = fetch_docs(["https://www.flickr.com/services/api/"], {"flickr.com"}, set(), http=http)

    page = batch.pages[0]
    assert page.status == STATUS_OK and page.domain == "flickr.com"
    assert page.text == "# Photosets\n\nCall **flickr.photosets.getList** to list sets."


def test_new_domains_wait_for_one_approval_and_are_not_requested():
    http = FakeHTTP({})

    batch = fetch_docs(["https://stackoverflow.com/q/1", "https://stackoverflow.com/q/2",
                        "https://raw.githubusercontent.com/x/spec.json"], {"flickr.com"}, set(), http=http)

    assert http.requested == []
    assert batch.needing_approval() == {"stackoverflow.com": ["https://stackoverflow.com/q/1",
                                                              "https://stackoverflow.com/q/2"],
                                        "raw.githubusercontent.com": ["https://raw.githubusercontent.com/x/spec.json"]}


def test_a_denied_domain_comes_back_denied():
    batch = fetch_docs(["https://example.org/a"], set(), {"example.org"}, http=FakeHTTP({}))
    assert batch.pages[0].status == STATUS_DENIED


def test_a_redirect_to_a_new_domain_stops_there():
    http = FakeHTTP({"https://www.flickr.com/docs": (302, "text/html", "", "https://evil.example/landing")})

    batch = fetch_docs(["https://www.flickr.com/docs"], {"flickr.com"}, set(), http=http)

    assert batch.pages[0].status == STATUS_NEEDS_APPROVAL
    assert batch.needing_approval() == {"evil.example": ["https://evil.example/landing"]}
    assert http.requested == ["https://www.flickr.com/docs"]


def test_redirects_within_allowed_domains_are_followed():
    http = FakeHTTP({"https://flickr.com/docs": (301, "text/html", "", "https://www.flickr.com/docs/"),
                     "https://www.flickr.com/docs/": (200, "text/plain", "plain docs", None)})

    assert fetch_docs(["https://flickr.com/docs"], {"flickr.com"}, set(), http=http).pages[0].text == "plain docs"


def test_private_addresses_and_http_are_never_read(monkeypatch):
    monkeypatch.setattr(docs, "_public_address", REAL_PUBLIC_ADDRESS)
    monkeypatch.setattr(docs.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.168.1.1", 443))])
    http = FakeHTTP({})

    private = fetch_docs(["https://router.example.com/"], {"example.com"}, set(), http=http).pages[0]
    plain = fetch_docs(["http://www.flickr.com/"], {"flickr.com"}, set(), http=http).pages[0]

    assert private.status == STATUS_ERROR and "public internet" in private.error
    assert plain.status == STATUS_ERROR and http.requested == []



def test_an_openapi_spec_is_condensed_to_its_endpoints():
    spec = {"openapi": "3.0.0", "info": {"title": "Immich", "version": "1.130"},
            "servers": [{"url": "/api"}],
            "components": {"securitySchemes": {"api_key": {"type": "apiKey", "in": "header", "name": "x-api-key"}}},
            "paths": {"/albums": {"get": {"summary": "List albums"}, "post": {"operationId": "createAlbum"}},
                      "/assets/{id}/original": {"get": {"summary": "Download",
                                                        "parameters": [{"name": "id", "in": "path"}]}}}}
    http = FakeHTTP({"https://immich.app/openapi.json": (200, "application/json", json.dumps(spec), None)})

    text = fetch_docs(["https://immich.app/openapi.json"], {"immich.app"}, set(), http=http).pages[0].text

    assert text.splitlines() == [
        "OpenAPI: Immich 1.130", "Servers: /api", "Security: api_key=apiKey in header 'x-api-key'",
        "GET /albums  List albums", "POST /albums  createAlbum",
        "GET /assets/{id}/original  Download  params: id(path)"]
    assert condense_openapi({"paths": {}}) is None


def test_pages_and_batches_are_capped(monkeypatch):
    monkeypatch.setattr(docs, "MAX_CHARS_PER_PAGE", 50)
    monkeypatch.setattr(docs, "MAX_CHARS_PER_BATCH", 80)
    pages = {f"https://www.flickr.com/{i}": (200, "text/plain", "x" * 500, None) for i in range(12)}

    batch = fetch_docs(list(pages), {"flickr.com"}, set(), http=FakeHTTP(pages))

    assert len(batch.pages) == docs.MAX_URLS_PER_BATCH
    assert batch.pages[0].text.startswith("x" * 50) and "cut" in batch.pages[0].text
    assert sum(len(p.text) for p in batch.pages) <= 80


def test_api_docs_keep_their_tables_code_and_links():
    page = html_to_markdown("""<body><nav>Home</nav><main><h2>Arguments</h2>
        <table><tr><th>name</th><th>required</th></tr><tr><td>user_id</td><td>no</td></tr></table>
        <pre><code>{"title": {"_content": "Portfolio"}}</code></pre>
        <p>See <a href="https://www.flickr.com/services/api/">the API</a>.</p><img src="x.png"></main>
        <footer>(c)</footer><script>x()</script></body>""")

    assert page.splitlines() == [
        "## Arguments", "", "| name | required |", "| --- | --- |", "| user_id | no |", "",
        "```", '{"title": {"_content": "Portfolio"}}', "```", "",
        "See [the API](https://www.flickr.com/services/api/)."]

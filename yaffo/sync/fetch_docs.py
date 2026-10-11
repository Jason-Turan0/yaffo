"""The service builder's `fetch_docs`: Yaffo (not the AI provider) reads documentation
pages for the model, one batch at a time, from authorized domains only.

Authorization is by registrable domain (doc_domains.py) and deterministic: a URL is
fetched if and only if its domain is in the service's authorized set. Other URLs come
back `needs_approval`, grouped by domain, for one prompt to the user; a domain the user
denied comes back `denied`. Redirects are followed by hand, only within authorized
domains: a redirect to a new domain stops there and needs approval like any URL.

Only https is read, and never from a private, loopback or link-local address, so a
docs page can't steer the builder at the user's own network. Responses are capped
(bytes in, characters out). HTML becomes Markdown (beautifulsoup4 + markdownify:
headings, tables, links and code kept; scripts, styles and navigation dropped), the
form agents read best; an OpenAPI spec in JSON is condensed to its endpoints (method, path, summary,
parameters); anything else textual is passed through, trimmed. Fetched text is
untrusted input for the model: nothing it says can widen what an adapter may call
(see "Fetched docs are untrusted" in the design).
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify

from yaffo.sync.doc_domains import registrable_domain

MAX_URLS_PER_BATCH = 8
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CHARS_PER_PAGE = 20_000
MAX_CHARS_PER_BATCH = 60_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = (10, 30)
USER_AGENT = "Yaffo service builder (reads API documentation)"

STATUS_OK = "ok"
STATUS_NEEDS_APPROVAL = "needs_approval"
STATUS_DENIED = "denied"
STATUS_ERROR = "error"


@dataclass
class FetchedPage:
    url: str
    status: str
    domain: str | None = None
    text: str = ""
    error: str = ""


@dataclass
class DocsBatch:
    pages: list[FetchedPage] = field(default_factory=list)

    def needing_approval(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for page in self.pages:
            if page.status == STATUS_NEEDS_APPROVAL and page.domain:
                found.setdefault(page.domain, []).append(page.url)
        return found


# Page chrome that isn't documentation: dropped before conversion.
CHROME_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "svg", "template", "iframe",
               "form", "button", "img", "picture", "video", "canvas")


def html_to_markdown(html: str) -> str:
    """A page as Markdown, the form models read best: headings, lists, tables, links
    and fenced code kept; scripts, navigation and other chrome dropped. Parsed with
    the standard library's HTML parser (no lxml)."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(CHROME_TAGS):
        element.decompose()
    body = soup.find("main") or soup.find("article") or soup.body or soup
    text = markdownify(str(body), heading_style="ATX", bullets="-",
                       code_language="", escape_underscores=False, escape_asterisks=False)
    return _BLANK_RUNS.sub("\n\n", text).strip()


_BLANK_RUNS = re.compile(r"\n\s*\n(\s*\n)+")


def condense_openapi(spec: dict) -> str | None:
    """An OpenAPI / Swagger document as one line per operation, or None if `spec`
    isn't one."""
    paths = spec.get("paths")
    if not isinstance(paths, dict) or not ("openapi" in spec or "swagger" in spec):
        return None
    servers = [s.get("url") for s in spec.get("servers") or [] if isinstance(s, dict) and s.get("url")]
    if spec.get("host"):
        servers.append(f"https://{spec['host']}{spec.get('basePath', '')}")
    lines = [f"OpenAPI: {(spec.get('info') or {}).get('title', '')} {(spec.get('info') or {}).get('version', '')}".strip(),
             f"Servers: {', '.join(servers) or '(none listed)'}"]
    schemes = (spec.get("components") or {}).get("securitySchemes") or spec.get("securityDefinitions") or {}
    if schemes:
        lines.append("Security: " + "; ".join(f"{name}={_scheme(s)}" for name, s in schemes.items() if isinstance(s, dict)))
    for path, operations in paths.items():
        if not isinstance(operations, dict):
            continue
        for method, operation in operations.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head") or not isinstance(operation, dict):
                continue
            params = [f"{p.get('name')}({p.get('in')})" for p in operation.get("parameters") or []
                      if isinstance(p, dict) and p.get("name")]
            summary = operation.get("summary") or operation.get("operationId") or ""
            lines.append(f"{method.upper()} {path}  {summary}" + (f"  params: {', '.join(params)}" if params else ""))
    return "\n".join(lines)


def _scheme(scheme: dict) -> str:
    kind = scheme.get("type", "")
    if kind == "apiKey":
        return f"apiKey in {scheme.get('in')} '{scheme.get('name')}'"
    if kind == "http":
        return f"http {scheme.get('scheme')}"
    return kind


def _public_address(host: str) -> bool:
    """Whether every address the host resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved \
                or address.is_multicast or address.is_unspecified:
            return False
    return True


def _readable(response: requests.Response, raw: bytes) -> str:
    kind = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    text = raw.decode(response.encoding or "utf-8", errors="replace")
    if kind in ("text/html", "application/xhtml+xml") or text.lstrip()[:15].lower().startswith(("<!doctype html", "<html")):
        return html_to_markdown(text)
    if kind.endswith("json") or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return text
        if isinstance(data, dict):
            condensed = condense_openapi(data)
            if condensed is not None:
                return condensed
        return json.dumps(data, indent=1)[:MAX_CHARS_PER_PAGE * 2]
    if kind.startswith("text/") or kind in ("application/yaml", "application/x-yaml", "application/xml", ""):
        return text
    raise ValueError(f"not a readable page ({kind})")


def _fetch(http: requests.Session, url: str, authorized: set[str]) -> FetchedPage:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        domain = registrable_domain(parts.hostname)
        if parts.scheme != "https" or domain is None:
            return FetchedPage(url, STATUS_ERROR, domain, error="only https addresses on a public domain are read")
        if domain not in authorized:
            return FetchedPage(current, STATUS_NEEDS_APPROVAL, domain)
        if not _public_address(parts.hostname):
            return FetchedPage(url, STATUS_ERROR, domain, error="that address isn't on the public internet")
        try:
            response = http.get(current, timeout=TIMEOUT_SECONDS, allow_redirects=False, stream=True,
                                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json,text/*"})
        except requests.RequestException as e:
            return FetchedPage(url, STATUS_ERROR, domain, error=str(e))
        try:
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                current = urljoin(current, response.headers["location"])
                continue
            if response.status_code != 200:
                return FetchedPage(url, STATUS_ERROR, domain, error=f"answered {response.status_code}")
            raw = b""
            for chunk in response.iter_content(64 * 1024):
                raw += chunk
                if len(raw) > MAX_RESPONSE_BYTES:
                    break
            text = _readable(response, raw[:MAX_RESPONSE_BYTES])
        except ValueError as e:
            return FetchedPage(url, STATUS_ERROR, domain, error=str(e))
        finally:
            response.close()
        if len(text) > MAX_CHARS_PER_PAGE:
            text = text[:MAX_CHARS_PER_PAGE] + "\n[… cut: the page is longer]"
        return FetchedPage(url, STATUS_OK, domain, text=text)
    return FetchedPage(url, STATUS_ERROR, None, error="too many redirects")


def fetch_docs(urls: list[str], authorized: set[str], denied: set[str],
               http: requests.Session | None = None) -> DocsBatch:
    """Read a batch of documentation URLs. Authorized domains are fetched now; the
    rest are returned for one approval prompt, or as denied."""
    http = http or requests.Session()
    batch = DocsBatch()
    budget = MAX_CHARS_PER_BATCH
    for url in list(dict.fromkeys(u.strip() for u in urls if isinstance(u, str) and u.strip()))[:MAX_URLS_PER_BATCH]:
        domain = registrable_domain(urlsplit(url).hostname)
        if domain in denied:
            batch.pages.append(FetchedPage(url, STATUS_DENIED, domain))
            continue
        page = _fetch(http, url, authorized)
        if page.status == STATUS_NEEDS_APPROVAL and page.domain in denied:
            page.status = STATUS_DENIED
        if page.status == STATUS_OK:
            page.text = page.text[:max(budget, 0)]
            budget -= len(page.text)
        batch.pages.append(page)
    return batch

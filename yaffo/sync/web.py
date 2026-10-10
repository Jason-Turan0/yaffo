"""Outbound HTTP for adapters: the `web_request` and `url_template` host functions,
and the engine's downloads.

Every call is checked here, at the host boundary, in this order:

1. The URL is https and on the connection's hosts: `base_urls`, which get the
   account's credentials, or `followup_hosts`, which never do.
2. A grant authorizes it: an **endpoint** grant whose method matches and whose URL
   pattern matches the URL's scheme, host and path (query strings aren't part of a
   grant), or, for a URL no pattern matches, a **follow-up** grant for the method
   and the URL is one Yaffo itself received from a granted call: in a response
   body or `Location` during this run, or stored by the engine as the sync's
   cursor or checkpoint after an earlier run vouched for it (`runtime.followups`).
   Grants only count when they were approved for the adapter's current code.
3. Read-only clients (Test, the option pickers) allow only `read`-risk calls.

Denials and failures come back to the script as data, `{"status": 0, "error": ...}`,
never as exceptions. Redirects are followed by hand, only to allowed hosts, and
credentials are only ever sent to `base_urls`. Responses expose a short allowlist of
headers. See docs/development/automation-sync-adapters.md and automation-web-calls.md
(Runtime enforcement).
"""
from __future__ import annotations

import hashlib
import json as jsonlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit

import requests

from yaffo.logging_config import get_logger
from yaffo.sync.adapter_static import placeholder_names, pattern_regex
from yaffo.sync.oauth import TokenSource, TokenUnavailable
from yaffo.sync.providers import ProviderPreset, allowed_url, incoming_url, is_base_url, outgoing_url
from yaffo.sync.schemas import GRANT_ENDPOINT, GRANT_FOLLOWUP, RISK_READ, WebGrant

logger = get_logger(__name__)

CONNECT_TIMEOUT_SECONDS = 15
READ_TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_CALLS_PER_CLIENT = 5000
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024 * 1024
RESPONSE_HEADERS = ("content-type", "retry-after", "location", "etag", "link", "content-range")
ALLOWED_REQUEST_HEADERS = re.compile(r"^(accept|accept-language|content-type|content-range|prefer|if-match|if-none-match|"
                                     r"x-[a-z0-9-]+)$", re.IGNORECASE)
_URL_IN_TEXT = re.compile(r"https://[^\s\"'<>\\]+")
_REDIRECTS = (301, 302, 303, 307, 308)


# The value the host binds to CONNECTION in an adapter: adapters always pass it, and
# the host substitutes the sync's own connection.
CONNECTION_TOKEN = "connection"


class DownloadError(OSError):
    """A download failed; the item is recorded failed and retried later."""


class CredentialsRejected(DownloadError):
    """The provider refused the token mid-page. The item is retried, and the token
    is refreshed at the next page boundary."""


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def url_template(template: str, **values: Any) -> str:
    """Fill a URL template's `{placeholders}`, encoding each value: one path segment,
    or several for `{path}` / `{..._path}`. Every placeholder needs a value, and
    every value a placeholder."""
    if not isinstance(template, str):
        raise ValueError("url_template needs a template string")
    names = placeholder_names(template)
    missing = [n for n in names if n not in values]
    extra = [k for k in values if k not in names]
    if missing or extra:
        raise ValueError(f"url_template: missing {missing}, unexpected {extra}")
    result = template
    for name in names:
        value = values[name]
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError(f"url_template: {name} must be text or a number")
        multi = name == "path" or name.endswith("_path")
        result = result.replace("{" + name + "}", quote(str(value), safe="/" if multi else ""), 1)
    return result


@dataclass
class WebLimits:
    max_calls: int = MAX_CALLS_PER_CLIENT
    max_response_bytes: int = MAX_RESPONSE_BYTES


@dataclass
class WebClient:
    """The web access of one adapter for one sync (or one Test / picker call)."""
    provider: ProviderPreset
    grants: list[WebGrant]
    code_hash: str
    tokens: TokenSource | None
    read_only: bool = False
    vouched_hashes: set[str] = field(default_factory=set)
    http: requests.Session = field(default_factory=requests.Session)
    limits: WebLimits = field(default_factory=WebLimits)
    calls: int = 0
    denied: list[str] = field(default_factory=list)
    auth_failed: bool = False
    needs_refresh: bool = False
    # Set while the engine is inside a page (it may hold the database's write
    # lock): tokens are then never refreshed, since the lease is a database write.
    mid_page: bool = False
    _seen: set[str] = field(default_factory=set)
    upload_paths: dict[int, Path] = field(default_factory=dict)

    # ---- grants --------------------------------------------------------------

    def _valid_grants(self) -> list[WebGrant]:
        return [g for g in self.grants if g.granted_code_hash == self.code_hash]

    def authorize(self, method: str, url: str) -> str | None:
        """None when the call may go ahead, otherwise why not."""
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            return "only https URLs can be called"
        if not allowed_url(self.provider, url):
            return f"{parts.hostname} isn't one of {self.provider.label}'s hosts"
        bare = f"{parts.scheme}://{parts.netloc}{parts.path}"
        grants = [g for g in self._valid_grants() if g.method == method]
        if self.read_only:
            grants = [g for g in grants if g.risk == RISK_READ]
        if any(g.grant_kind == GRANT_ENDPOINT and g.url_pattern and pattern_regex(g.url_pattern).match(bare)
               for g in grants):
            return None
        if any(g.grant_kind == GRANT_FOLLOWUP for g in grants) and self.vouches(url):
            return None
        if self.read_only and method not in ("GET", "HEAD"):
            return f"{method} isn't allowed here: only reads are"
        return f"{method} {bare} isn't authorized"

    def vouches(self, url: str) -> bool:
        return url in self._seen or url_hash(url) in self.vouched_hashes

    def _collect_urls(self, text: str, location: str | None) -> None:
        candidates = set(_URL_IN_TEXT.findall(text.replace("\\/", "/"))) if text else set()
        if location:
            candidates.add(location)
        for candidate in candidates:
            if allowed_url(self.provider, candidate):
                self._seen.add(candidate)

    # ---- requests ------------------------------------------------------------

    def _headers(self, url: str, extra: dict | None, token: str | None) -> dict[str, str]:
        headers = {}
        for name, value in (extra or {}).items():
            if not isinstance(name, str) or not ALLOWED_REQUEST_HEADERS.match(name):
                raise ValueError(f"header {name!r} can't be set by an adapter")
            headers[name] = str(value)
        if token and is_base_url(self.provider, url):
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _send(self, method: str, url: str, headers: dict, body: Any, stream: bool) -> requests.Response:
        current, current_method, current_body = url, method, body
        for _ in range(MAX_REDIRECTS + 1):
            send_headers = dict(headers)
            if not is_base_url(self.provider, current):
                send_headers.pop("Authorization", None)
            response = self.http.request(current_method, outgoing_url(current), headers=send_headers,
                                         data=current_body, allow_redirects=False, stream=stream,
                                         timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS))
            if response.status_code not in _REDIRECTS or "location" not in response.headers:
                return response
            target = incoming_url(urljoin(current, response.headers["location"]))
            _ = response.content  # drain the (small) redirect body so the connection is reused
            response.close()
            if not allowed_url(self.provider, target):
                raise PermissionError(f"redirect to {urlsplit(target).hostname} isn't allowed")
            self._seen.add(target)
            if response.status_code == 303 or (response.status_code in (301, 302) and current_method == "POST"):
                current_method, current_body = "GET", None
            elif hasattr(current_body, "seek"):
                current_body.seek(0)
            current = target
        raise PermissionError("too many redirects")

    def _token(self) -> str | None:
        if self.tokens is None:
            return None
        if self.mid_page and self.tokens.cached():
            return self.tokens.cached()
        return self.tokens.token()

    def web_request(self, connection: Any, method: str, url: str, json: Any = None, headers: dict | None = None,
                    query: dict | None = None) -> dict[str, Any]:
        """The adapter host function. `connection` is the CONNECTION the host bound."""
        if connection != CONNECTION_TOKEN:
            return {"status": 0, "error": "use CONNECTION as the connection"}
        if not isinstance(method, str) or not isinstance(url, str):
            return {"status": 0, "error": "method and url must be text"}
        method = method.upper()
        if query:
            if not isinstance(query, dict):
                return {"status": 0, "error": "query must be a dict"}
            url = url + ("&" if "?" in url else "?") + urlencode({str(k): str(v) for k, v in query.items()})
        problem = self.authorize(method, url)
        if problem:
            self.denied.append(f"{method} {url.split('?', 1)[0]}")
            return {"status": 0, "error": f"denied: {problem}"}
        self.calls += 1
        if self.calls > self.limits.max_calls:
            return {"status": 0, "error": "too many web calls in one run"}
        try:
            body = None
            extra = dict(headers or {})
            if json is not None:
                body = jsonlib.dumps(json).encode("utf-8")
                extra.setdefault("Content-Type", "application/json")
            token = self._token()
            response = self._send(method, url, self._headers(url, extra, token), body, stream=True)
            if response.status_code == 401 and self.mid_page:
                self.needs_refresh = True
            elif response.status_code == 401 and self.tokens is not None and is_base_url(self.provider, url):
                response.close()
                token = self.tokens.refresh()
                response = self._send(method, url, self._headers(url, extra, token), body, stream=True)
                if response.status_code == 401:
                    self.auth_failed = True
            return self._result(response)
        except TokenUnavailable as e:
            self.auth_failed = True
            return {"status": 0, "error": f"sign-in needed: {e}"}
        except (requests.RequestException, PermissionError, ValueError) as e:
            return {"status": 0, "error": str(e)}

    def web_upload_media(self, connection: Any, method: str, url: str, media_item_id: int,
                         encoding: str = "raw", field: str | None = None, fields: dict | None = None,
                         headers: dict | None = None, offset: int = 0, length: int | None = None) -> dict[str, Any]:
        """Upload only a prepared batch item's bytes. A session upload may request
        a bounded slice; adapters cannot name arbitrary local paths."""
        if connection != CONNECTION_TOKEN or not isinstance(media_item_id, int) or isinstance(media_item_id, bool):
            return {"status": 0, "error": "use CONNECTION and a batch media_item_id"}
        path = self.upload_paths.get(media_item_id)
        if path is None:
            return {"status": 0, "error": "media item isn't in this upload batch"}
        if not isinstance(method, str) or not isinstance(url, str) or encoding not in ("raw", "multipart"):
            return {"status": 0, "error": "invalid upload request"}
        method = method.upper()
        problem = self.authorize(method, url)
        if problem:
            self.denied.append(f"{method} {url.split('?', 1)[0]}")
            return {"status": 0, "error": f"denied: {problem}"}
        self.calls += 1
        if self.calls > self.limits.max_calls:
            return {"status": 0, "error": "too many web calls in one run"}
        try:
            size = path.stat().st_size
            if not isinstance(offset, int) or offset < 0 or offset > size or isinstance(offset, bool):
                raise ValueError("invalid upload offset")
            if length is not None and (not isinstance(length, int) or isinstance(length, bool)
                                       or length <= 0 or length > 10 * 1024 * 1024 or offset + length > size):
                raise ValueError("invalid upload length")
            if offset and length is None:
                raise ValueError("an upload offset needs a bounded length")
            extra = dict(headers or {})
            token = self._token()
            if encoding == "multipart":
                if length is not None or offset or not field or not isinstance(fields, dict):
                    raise ValueError("multipart needs a field and fields, without a byte range")
                with path.open("rb") as source:
                    response = self.http.request(method, outgoing_url(url),
                                                 headers=self._headers(url, extra, token), data=fields,
                                                 files={field: (path.name, source)}, stream=True,
                                                 allow_redirects=False,
                                                 timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS))
            else:
                with path.open("rb") as source:
                    source.seek(offset)
                    body = source.read(length) if length is not None else source
                    extra.setdefault("Content-Type", "application/octet-stream")
                    response = self._send(method, url, self._headers(url, extra, token), body, stream=True)
            if response.status_code == 401 and is_base_url(self.provider, url):
                self.needs_refresh = True
                self.auth_failed = True
            return self._result(response)
        except (OSError, requests.RequestException, PermissionError, ValueError, TokenUnavailable) as e:
            return {"status": 0, "error": str(e)}

    def _result(self, response: requests.Response) -> dict[str, Any]:
        try:
            raw = b""
            for chunk in response.iter_content(64 * 1024):
                raw += chunk
                if len(raw) > self.limits.max_response_bytes:
                    return {"status": 0, "error": "response too large"}
        finally:
            response.close()
        text = raw.decode(response.encoding or "utf-8", errors="replace")
        parsed = None
        if text:
            try:
                parsed = jsonlib.loads(text)
            except ValueError:
                parsed = None
        location = response.headers.get("location")
        if location:
            location = incoming_url(urljoin(response.url, location))
        self._collect_urls(text, location)
        headers = {name: response.headers[name] for name in RESPONSE_HEADERS if name in response.headers}
        if location:
            headers["location"] = location
        return {"status": response.status_code, "json": parsed, "text": text if parsed is None else None,
                "headers": headers}

    # ---- downloads -----------------------------------------------------------

    def download(self, url: str, headers: dict | None, destination: Path, max_bytes: int | None = None) -> None:
        """Stream a record's file to `destination`. The URL must be on the
        connection's hosts; credentials go only to base_urls. Doesn't refresh the
        token (it's mid-page): a 401 raises CredentialsRejected."""
        if not isinstance(url, str) or not allowed_url(self.provider, url):
            raise DownloadError(f"download URL isn't on {self.provider.label}'s hosts")
        limit = max_bytes or MAX_DOWNLOAD_BYTES
        try:
            token = self.tokens.cached() if self.tokens is not None else None
            if self.tokens is not None and token is None:
                token = self.tokens.token()
            response = self._send("GET", url, self._headers(url, headers, token), None, stream=True)
        except (requests.RequestException, PermissionError, ValueError, TokenUnavailable) as e:
            raise DownloadError(str(e)) from e
        try:
            if response.status_code == 401:
                self.needs_refresh = True
                raise CredentialsRejected("the provider refused the token")
            if response.status_code != 200:
                raise DownloadError(f"download answered {response.status_code}")
            written = 0
            with open(destination, "wb") as out:
                for chunk in response.iter_content(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        raise DownloadError("the file is larger than the service said")
                    out.write(chunk)
        except requests.RequestException as e:
            raise DownloadError(str(e)) from e
        finally:
            response.close()

    def boundary(self) -> None:
        """At a page boundary: refresh the token if a download was refused."""
        if self.needs_refresh and self.tokens is not None:
            self.needs_refresh = False
            try:
                self.tokens.refresh()
            except TokenUnavailable:
                self.auth_failed = True

#!/usr/bin/env python3
"""A fake Microsoft identity platform + OneDrive (Graph) for tests and the UI sandbox.

It answers the calls the OneDrive preset and Yaffo's sign-in make, on one local port:

- sign-in: `/common/oauth2/v2.0/authorize` (a page with an Accept button), `/token`
  (authorization code with PKCE S256, rotating refresh tokens, device code), and
  `/devicecode` plus a `/devicelogin` page to approve a code;
- Graph: `/v1.0/me`, `/v1.0/me/drive/special/cameraroll`, `.../root/children`,
  `.../items/{id}/children`, `.../items/{id}/delta` (paged with `nextLink`s on a
  different path, ending in a `deltaLink`; later passes list only changes, with
  tombstones), and `.../items/{id}/content`, which redirects to a pre-authenticated
  download on `public.files.1drv.com` that refuses requests carrying credentials;
- `/_admin/*`: add, edit, and delete photos, throttle the next calls (429), expire or
  revoke tokens, and read counters.

Point Yaffo at it with YAFFO_SYNC_ORIGIN_OVERRIDES (see `overrides()`), so every check
in Yaffo still sees the real hosts. Run (one line):
    python -m scripts.fake_onedrive --port 5098 --photos 40
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from flask import Flask, Response, abort, jsonify, redirect, request
from PIL import Image
from waitress.server import create_server

GRAPH = "https://graph.microsoft.com/v1.0"
DOWNLOAD_ORIGIN = "https://public.files.1drv.com"
REAL_ORIGINS = ("https://graph.microsoft.com", "https://login.microsoftonline.com", DOWNLOAD_ORIGIN)
ROOT_ID = "ROOT"
CAMERA_ROLL_ID = "CAMROLL"
SUBFOLDER_ID = "CAMROLL-2024"
OTHER_FOLDER_ID = "DOCS"
USER = {"id": "fake-user-1", "userPrincipalName": "jason@example.com", "displayName": "Jason Example",
        "mail": "jason@example.com"}
EXIF_IFD = 0x8769
EXIF_DATETIME_ORIGINAL = 36867


def jpeg_bytes(seed: int, with_exif: bool) -> bytes:
    """A small JPEG unique to `seed`, with a capture date in EXIF when asked."""
    image = Image.new("RGB", (24, 16), ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256))
    image.putpixel((seed % 24, (seed // 24) % 16), ((seed // 7) % 256, 255 - seed % 256, 128))
    out = io.BytesIO()
    if with_exif:
        exif = Image.Exif()
        taken = f"2023:{1 + seed % 12:02d}:{1 + seed % 27:02d} 10:{seed % 60:02d}:00"
        exif.get_ifd(EXIF_IFD)[EXIF_DATETIME_ORIGINAL] = taken
        image.save(out, format="JPEG", exif=exif.tobytes(), quality=70, comment=f"fake {seed}".encode())
    else:
        image.save(out, format="JPEG", quality=70, comment=f"fake {seed}".encode())
    return out.getvalue()


@dataclass
class Item:
    id: str
    name: str
    parent: str
    folder: bool = False
    content: bytes = b""
    modified: str = "2024-01-01T00:00:00Z"
    taken: str | None = None
    location: dict | None = None
    deleted: bool = False
    seq: int = 0
    mime: str = "image/jpeg"

    def to_graph(self, children: int = 0) -> dict:
        if self.deleted:
            return {"id": self.id, "name": self.name, "deleted": {"state": "deleted"},
                    "parentReference": {"id": self.parent}}
        data: dict = {"id": self.id, "name": self.name, "lastModifiedDateTime": self.modified,
                      "parentReference": {"id": self.parent}}
        if self.folder:
            data["folder"] = {"childCount": children}
            return data
        data["size"] = len(self.content)
        data["file"] = {"mimeType": self.mime,
                        "hashes": {"sha1Hash": hashlib.sha1(self.content).hexdigest().upper()}}
        if self.mime.startswith("image/"):
            data["image"] = {"width": 24, "height": 16}
            data["photo"] = {"takenDateTime": self.taken} if self.taken else {}
        if self.location:
            data["location"] = dict(self.location)
        return data


@dataclass
class Grant:
    challenge: str
    redirect_uri: str
    scope: str


@dataclass
class State:
    items: dict[str, Item] = field(default_factory=dict)
    seq: int = 0
    page_size: int = 200
    codes: dict[str, Grant] = field(default_factory=dict)
    access: dict[str, float] = field(default_factory=dict)
    refresh: set[str] = field(default_factory=set)
    devices: dict[str, dict] = field(default_factory=dict)
    throttle: int = 0
    revoked: bool = False
    broken_downloads: set[str] = field(default_factory=set)
    # The next N /me calls answer 504, as Graph sometimes does right after sign-in.
    me_failures: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def bump(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def add_folder(self, item_id: str, name: str, parent: str) -> None:
        self.items[item_id] = Item(id=item_id, name=name, parent=parent, folder=True, seq=self.next_seq())

    def add_photos(self, count: int, parent: str = CAMERA_ROLL_ID, prefix: str = "IMG") -> list[str]:
        ids = []
        start = len([i for i in self.items.values() if not i.folder])
        for offset in range(count):
            n = start + offset + 1
            item_id = f"F{n:06d}"
            # Every third photo has no EXIF date: Yaffo must use the hint then.
            with_exif = n % 3 != 0
            taken = f"2022-{1 + n % 12:02d}-{1 + n % 27:02d}T15:{n % 60:02d}:00Z"
            location = {"latitude": 41.8 + n / 10000, "longitude": -87.6} if n % 5 == 0 else None
            self.items[item_id] = Item(id=item_id, name=f"{prefix}_{n:05d}.jpg",
                                       parent=SUBFOLDER_ID if n % 4 == 0 and parent == CAMERA_ROLL_ID else parent,
                                       content=jpeg_bytes(n, with_exif), taken=taken, location=location,
                                       modified=f"2024-02-{1 + n % 27:02d}T08:00:00Z", seq=self.next_seq())
            ids.append(item_id)
        self.items[f"DOC{len(self.items)}"] = Item(id=f"DOC{len(self.items)}", name="notes.txt", parent=parent,
                                                   content=b"not a photo", mime="text/plain", seq=self.next_seq())
        return ids

    def under(self, folder_id: str, item: Item) -> bool:
        parent, hops = item.parent, 0
        while parent and hops < 20:
            if parent == folder_id:
                return True
            owner = self.items.get(parent)
            parent, hops = (owner.parent if owner else None), hops + 1
        return False


def create_app(state: State) -> Flask:
    app = Flask(__name__)

    def authorized() -> bool:
        header = request.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ").strip()
        expires = state.access.get(token)
        return not state.revoked and expires is not None and expires > time.time()

    def graph_guard() -> Response | None:
        state.bump("graph_calls")
        if state.throttle > 0:
            state.throttle -= 1
            state.bump("throttled")
            response = jsonify({"error": {"code": "TooManyRequests"}})
            response.status_code = 429
            response.headers["Retry-After"] = "120"
            return response
        if not authorized():
            state.bump("unauthorized")
            response = jsonify({"error": {"code": "InvalidAuthenticationToken"}})
            response.status_code = 401
            return response
        return None

    def issue_tokens(scope: str) -> dict:
        access = "AT-" + secrets.token_urlsafe(16)
        refresh = "RT-" + secrets.token_urlsafe(16)
        state.access[access] = time.time() + 3600
        state.refresh.add(refresh)
        state.bump("tokens_issued")
        return {"token_type": "Bearer", "access_token": access, "refresh_token": refresh, "expires_in": 3600,
                "scope": scope}

    # ---- identity ---------------------------------------------------------

    @app.get("/common/oauth2/v2.0/authorize")
    def authorize():
        params = request.args.to_dict()
        if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
            return Response("PKCE required", status=400)
        if not params.get("redirect_uri", "").startswith(("http://localhost", "http://127.0.0.1")):
            return Response("invalid_request: redirect_uri", status=400)
        query = urlencode(params)
        scopes = params.get("scope", "")
        return Response(
            "<!doctype html><title>Fake Microsoft sign-in</title>"
            "<h1>Sign in to Fake Microsoft</h1>"
            f"<p>Yaffo wants: <code>{scopes}</code></p>"
            f"<form method='get' action='/common/oauth2/v2.0/approve?{query}'>"
            + "".join(f"<input type='hidden' name='{k}' value='{v}'>" for k, v in params.items())
            + "<button type='submit' id='accept'>Accept as jason@example.com</button></form>"
            f"<p><a id='deny' href='{params['redirect_uri']}?error=access_denied&state={params.get('state', '')}'>"
            "Cancel</a></p>",
            mimetype="text/html")

    @app.get("/common/oauth2/v2.0/approve")
    def approve():
        params = request.args.to_dict()
        code = "C-" + secrets.token_urlsafe(12)
        with state.lock:
            state.codes[code] = Grant(params["code_challenge"], params["redirect_uri"], params.get("scope", ""))
        return redirect(f"{params['redirect_uri']}?{urlencode({'code': code, 'state': params.get('state', '')})}")

    @app.post("/common/oauth2/v2.0/token")
    def token():
        form = request.form
        grant_type = form.get("grant_type")
        with state.lock:
            if grant_type == "authorization_code":
                grant = state.codes.pop(form.get("code", ""), None)
                if grant is None or grant.redirect_uri != form.get("redirect_uri"):
                    return jsonify({"error": "invalid_grant"}), 400
                verifier = form.get("code_verifier", "")
                digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                if digest != grant.challenge:
                    return jsonify({"error": "invalid_grant", "error_description": "PKCE"}), 400
                return jsonify(issue_tokens(grant.scope.replace("offline_access", "").strip()))
            if grant_type == "refresh_token":
                state.bump("refreshes")
                old = form.get("refresh_token", "")
                if state.revoked or old not in state.refresh:
                    return jsonify({"error": "invalid_grant"}), 400
                state.refresh.discard(old)  # rotating refresh tokens
                return jsonify(issue_tokens("Files.Read User.Read"))
            if grant_type == "urn:ietf:params:oauth:grant-type:device_code":
                device = state.devices.get(form.get("device_code", ""))
                if device is None:
                    return jsonify({"error": "expired_token"}), 400
                if not device["approved"]:
                    return jsonify({"error": "authorization_pending"}), 400
                del state.devices[form["device_code"]]
                return jsonify(issue_tokens(device["scope"].replace("offline_access", "").strip()))
        return jsonify({"error": "unsupported_grant_type"}), 400

    @app.post("/common/oauth2/v2.0/devicecode")
    def devicecode():
        device_code = "D-" + secrets.token_urlsafe(12)
        user_code = secrets.token_hex(4).upper()
        with state.lock:
            state.devices[device_code] = {"user_code": user_code, "approved": False,
                                          "scope": request.form.get("scope", "")}
        return jsonify({"device_code": device_code, "user_code": user_code, "interval": 1, "expires_in": 900,
                        "verification_uri": f"{request.host_url}devicelogin"})

    @app.route("/devicelogin", methods=["GET", "POST"])
    def devicelogin():
        if request.method == "POST":
            code = request.form.get("code", "").strip().upper()
            with state.lock:
                for device in state.devices.values():
                    if device["user_code"] == code:
                        device["approved"] = True
                        return Response("<p id='done'>You're signed in. Return to Yaffo.</p>", mimetype="text/html")
            return Response("<p id='bad'>Unknown code.</p>", mimetype="text/html", status=400)
        return Response("<!doctype html><title>Fake device login</title><form method='post'>"
                        "<input name='code' id='code'><button id='next'>Next</button></form>", mimetype="text/html")

    # ---- Graph --------------------------------------------------------------

    @app.get("/v1.0/me")
    def me():
        if state.me_failures > 0:
            state.me_failures -= 1
            return jsonify({"error": {"code": "UnknownError"}}), 504
        return graph_guard() or jsonify(USER)

    def children_of(folder_id: str) -> list[Item]:
        return [i for i in state.items.values() if i.parent == folder_id and not i.deleted]

    def folder_json(item: Item) -> dict:
        return item.to_graph(children=len(children_of(item.id)))

    @app.get("/v1.0/me/drive/special/cameraroll")
    def camera_roll():
        return graph_guard() or jsonify(folder_json(state.items[CAMERA_ROLL_ID]))

    @app.get("/v1.0/me/drive/root/children")
    def root_children():
        return graph_guard() or jsonify({"value": [folder_json(i) if i.folder else i.to_graph()
                                                   for i in children_of(ROOT_ID)]})

    def path_of(item: Item) -> str:
        """The item's parent path as Graph reports it on a plain GET (never in delta)."""
        names, parent = [], state.items.get(item.parent)
        while parent is not None and parent.id != ROOT_ID:
            names.append(parent.name)
            parent = state.items.get(parent.parent)
        return "/drive/root:" + "".join(f"/{name}" for name in reversed(names))

    @app.get("/v1.0/me/drive/items/<item_id>")
    def item_get(item_id: str):
        guard = graph_guard()
        if guard:
            return guard
        item = state.items.get(item_id)
        if item is None or item.deleted:
            return jsonify({"error": {"code": "itemNotFound"}}), 404
        state.bump("item_lookups")
        data = folder_json(item) if item.folder else item.to_graph()
        if item.id != ROOT_ID:
            data["parentReference"] = {"id": item.parent, "path": path_of(item)}
        return jsonify(data)

    @app.get("/v1.0/me/drive/items/<item_id>/children")
    def item_children(item_id: str):
        guard = graph_guard()
        if guard:
            return guard
        if item_id not in state.items or state.items[item_id].deleted:
            return jsonify({"error": {"code": "itemNotFound"}}), 404
        return jsonify({"value": [folder_json(i) if i.folder else i.to_graph() for i in children_of(item_id)]})

    def delta_page(folder_id: str, since: int, position: int, snapshot: int):
        with state.lock:
            changed = sorted((i for i in state.items.values()
                              if since < i.seq <= snapshot and (i.id == folder_id or state.under(folder_id, i))),
                             key=lambda i: i.seq)
            page = changed[position:position + state.page_size]
            body: dict = {"value": [folder_json(i) if i.folder and not i.deleted else i.to_graph() for i in page]}
            if position + state.page_size < len(changed):
                body["@odata.nextLink"] = (f"{GRAPH}/drives/D1/items/{folder_id}/delta?"
                                           f"$skiptoken={since}.{position + state.page_size}.{snapshot}")
            else:
                body["@odata.deltaLink"] = f"{GRAPH}/drives/D1/items/{folder_id}/delta?token={snapshot}"
        state.bump("delta_pages")
        return jsonify(body)

    @app.get("/v1.0/me/drive/items/<item_id>/delta")
    def delta_start(item_id: str):
        guard = graph_guard()
        if guard:
            return guard
        if item_id not in state.items or state.items[item_id].deleted:
            return jsonify({"error": {"code": "itemNotFound"}}), 404
        return delta_page(item_id, 0, 0, state.seq)

    @app.get("/v1.0/drives/D1/items/<item_id>/delta")
    def delta_next(item_id: str):
        guard = graph_guard()
        if guard:
            return guard
        if item_id not in state.items or state.items[item_id].deleted:
            return jsonify({"error": {"code": "itemNotFound"}}), 404
        if "token" in request.args:
            since = int(request.args["token"])
            if since > state.seq:
                return jsonify({"error": {"code": "resyncRequired"}}), 410
            return delta_page(item_id, since, 0, state.seq)
        since, position, snapshot = (int(part) for part in request.args["$skiptoken"].split("."))
        return delta_page(item_id, since, position, snapshot)

    @app.get("/v1.0/me/drive/items/<item_id>/content")
    def content(item_id: str):
        guard = graph_guard()
        if guard:
            return guard
        item = state.items.get(item_id)
        if item is None or item.deleted or item.folder:
            return jsonify({"error": {"code": "itemNotFound"}}), 404
        return redirect(f"{DOWNLOAD_ORIGIN}/dl/{item_id}?sig={secrets.token_hex(4)}", code=302)

    @app.get("/dl/<item_id>")
    def download(item_id: str):
        state.bump("downloads")
        if "Authorization" in request.headers:
            state.bump("download_with_credentials")
            return Response("credentials must not be sent here", status=400)
        if item_id in state.broken_downloads:
            return Response("storage hiccup", status=500)
        item = state.items.get(item_id)
        if item is None or item.deleted:
            abort(404)
        return Response(item.content, mimetype=item.mime)

    # ---- admin --------------------------------------------------------------

    @app.post("/_admin/add")
    def admin_add():
        with state.lock:
            ids = state.add_photos(int(request.args.get("n", "1")), request.args.get("parent", CAMERA_ROLL_ID),
                                   request.args.get("prefix", "IMG"))
        return jsonify({"ids": ids})

    @app.post("/_admin/edit/<item_id>")
    def admin_edit(item_id: str):
        with state.lock:
            item = state.items[item_id]
            item.content = jpeg_bytes(100000 + item.seq, True)
            item.modified = "2025-06-01T00:00:00Z"
            item.seq = state.next_seq()
        return jsonify({"ok": True})

    @app.post("/_admin/delete/<item_id>")
    def admin_delete(item_id: str):
        with state.lock:
            item = state.items[item_id]
            item.deleted = True
            item.seq = state.next_seq()
        return jsonify({"ok": True})

    @app.post("/_admin/throttle")
    def admin_throttle():
        state.throttle = int(request.args.get("n", "1"))
        return jsonify({"ok": True})

    @app.post("/_admin/expire")
    def admin_expire():
        with state.lock:
            for token in state.access:
                state.access[token] = 0
        return jsonify({"ok": True})

    @app.post("/_admin/revoke")
    def admin_revoke():
        state.revoked = request.args.get("on", "1") == "1"
        return jsonify({"ok": True})

    @app.get("/_admin/state")
    def admin_state():
        return jsonify({"counters": state.counters, "seq": state.seq,
                        "photos": len([i for i in state.items.values() if not i.folder and not i.deleted])})

    return app


def new_state(photos: int = 0, page_size: int = 200) -> State:
    state = State(page_size=page_size)
    state.add_folder(ROOT_ID, "root", "")
    state.add_folder(CAMERA_ROLL_ID, "Camera Roll", ROOT_ID)
    state.add_folder(SUBFOLDER_ID, "2024", CAMERA_ROLL_ID)
    state.add_folder(OTHER_FOLDER_ID, "Documents", ROOT_ID)
    if photos:
        state.add_photos(photos)
    return state


def overrides(base_url: str) -> str:
    """The YAFFO_SYNC_ORIGIN_OVERRIDES value pointing Yaffo at this fake."""
    return json.dumps({origin: base_url for origin in REAL_ORIGINS})


class FakeOneDrive:
    """The fake on a background thread, for tests."""

    def __init__(self, photos: int = 0, page_size: int = 200):
        logging.getLogger("waitress").setLevel(logging.ERROR)
        self.state = new_state(photos, page_size)
        # waitress, not werkzeug's dev server: it keeps connections alive, so a large
        # backfill reuses them (as against the real Graph) instead of running out
        # of local ports.
        self.server = create_server(create_app(self.state), host="127.0.0.1", port=0, threads=8)
        self.base_url = f"http://127.0.0.1:{self.server.effective_port}"
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        try:
            self.server.run()
        except (OSError, ValueError):
            pass  # the socket closed under the loop at shutdown

    def __enter__(self) -> FakeOneDrive:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.close()

    @property
    def overrides(self) -> str:
        return overrides(self.base_url)

    def grant_token(self) -> dict:
        """A token set as if the user had signed in (for tests that skip sign-in)."""
        with self.state.lock:
            tokens = {"access_token": "AT-" + secrets.token_urlsafe(8), "refresh_token": "RT-" + secrets.token_urlsafe(8)}
            self.state.access[tokens["access_token"]] = time.time() + 3600
            self.state.refresh.add(tokens["refresh_token"])
        return {**tokens, "expires_at": int(time.time()) + 3600, "token_type": "Bearer"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=5098)
    parser.add_argument("--photos", type=int, default=40)
    parser.add_argument("--page-size", type=int, default=200)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    print(f"export YAFFO_SYNC_ORIGIN_OVERRIDES='{overrides(base)}'", flush=True)
    create_app(new_state(args.photos, args.page_size)).run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()

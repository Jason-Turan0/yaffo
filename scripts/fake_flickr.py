#!/usr/bin/env python3
"""A fake Flickr (OAuth 1.0a + the REST and upload APIs) for tests and the UI sandbox.

It answers on one local port for www.flickr.com, api.flickr.com and up.flickr.com
(point Yaffo at it with YAFFO_SYNC_ORIGIN_OVERRIDES, see `overrides()`), and checks the
OAuth 1 signature of every request against the real URL it stands in for, with the
app's consumer secret and the token secret it issued:

- sign-in: `/services/oauth/request_token`, `/services/oauth/authorize` (a page with an
  Approve link) and `/services/oauth/access_token`;
- REST (`/services/rest`, JSON with nojsoncallback): flickr.test.login,
  flickr.photosets.getList / create / addPhoto / removePhoto / getPhotos, and
  flickr.photos.delete. Like Flickr, failures answer 200 with `"stat": "fail"`;
- upload (`/services/upload/`): multipart `photo` plus fields, answering XML
  `<rsp stat="ok"><photoid>…</photoid></rsp>`; the signature covers every field but
  the photo, as Flickr's does;
- `/_admin/state` and `/_admin/revoke`.

Run (one line):
    python -m scripts.fake_flickr --port 5096
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
from dataclasses import dataclass, field
from urllib.parse import unquote, urlencode

from flask import Flask, Response, jsonify, redirect, request
from waitress.server import create_server

from yaffo.sync.oauth1 import signature_base

CONSUMER_KEY = "flickr-app-key"
CONSUMER_SECRET = "flickr-app-secret"
USER = {"id": "12345678@N01", "username": "jturan"}
REAL = {"www": "https://www.flickr.com", "api": "https://api.flickr.com", "up": "https://up.flickr.com"}


@dataclass
class State:
    request_tokens: dict[str, dict] = field(default_factory=dict)    # token -> {secret, callback, verifier}
    access_tokens: dict[str, str] = field(default_factory=dict)      # token -> secret
    photos: dict[str, dict] = field(default_factory=dict)
    sets: dict[str, dict] = field(default_factory=dict)              # id -> {title, photos: [ids]}
    counters: dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def bump(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1


def _oauth_params(header: str) -> dict[str, str]:
    if not header.startswith("OAuth "):
        return {}
    params = {}
    for part in header[6:].split(","):
        name, _, value = part.strip().partition("=")
        params[unquote(name)] = unquote(value.strip('"'))
    return params


def _valid(real_url: str, token_secret: str | None, signed_fields: list[tuple[str, str]]) -> tuple[bool, dict]:
    oauth = _oauth_params(request.headers.get("Authorization", ""))
    if oauth.get("oauth_consumer_key") != CONSUMER_KEY or oauth.get("oauth_signature_method") != "HMAC-SHA1":
        return False, oauth
    given = oauth.pop("oauth_signature", "")
    url = real_url + (("?" + request.query_string.decode()) if request.query_string else "")
    base = signature_base(request.method, url, [*oauth.items(), *signed_fields])
    key = f"{CONSUMER_SECRET}&{token_secret or ''}".encode()
    expected = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = given
    return hmac.compare_digest(expected, given), oauth


def create_app(state: State) -> Flask:
    app = Flask("fake_flickr")

    def fail(code: int, message: str):
        return jsonify({"stat": "fail", "code": code, "message": message})

    @app.post("/services/oauth/request_token")
    def request_token():
        ok, oauth = _valid(REAL["www"] + "/services/oauth/request_token", None, [])
        if not ok or not oauth.get("oauth_callback"):
            return Response("oauth_problem=signature_invalid", status=401)
        token, secret = "RT-" + secrets.token_hex(6), secrets.token_hex(8)
        with state.lock:
            state.request_tokens[token] = {"secret": secret, "callback": oauth["oauth_callback"], "verifier": None}
        return urlencode({"oauth_callback_confirmed": "true", "oauth_token": token, "oauth_token_secret": secret})

    @app.get("/services/oauth/authorize")
    def authorize():
        token = request.args.get("oauth_token", "")
        if token not in state.request_tokens:
            return Response("unknown token", status=400)
        return f'<a id="approve" href="/_approve?{urlencode({"oauth_token": token})}">Approve (perms={request.args.get("perms")})</a>'

    @app.get("/_approve")
    def approve():
        token = request.args.get("oauth_token", "")
        with state.lock:
            entry = state.request_tokens[token]
            entry["verifier"] = secrets.token_hex(6)
        joiner = "&" if "?" in entry["callback"] else "?"
        return redirect(f'{entry["callback"]}{joiner}{urlencode({"oauth_token": token, "oauth_verifier": entry["verifier"]})}')

    @app.post("/services/oauth/access_token")
    def access_token():
        oauth = _oauth_params(request.headers.get("Authorization", ""))
        entry = state.request_tokens.get(oauth.get("oauth_token", ""))
        ok, oauth = _valid(REAL["www"] + "/services/oauth/access_token", entry and entry["secret"], [])
        if not ok or entry is None or oauth.get("oauth_verifier") != entry["verifier"]:
            return Response("oauth_problem=token_rejected", status=401)
        token, secret = "AT-" + secrets.token_hex(6), secrets.token_hex(8)
        with state.lock:
            state.request_tokens.pop(oauth["oauth_token"], None)
            state.access_tokens[token] = secret
        return urlencode({"oauth_token": token, "oauth_token_secret": secret, "user_nsid": USER["id"],
                          "username": USER["username"]})

    def _authorized(real_url: str, signed_fields: list[tuple[str, str]]) -> bool:
        oauth = _oauth_params(request.headers.get("Authorization", ""))
        secret = state.access_tokens.get(oauth.get("oauth_token", ""))
        return secret is not None and _valid(real_url, secret, signed_fields)[0]

    @app.route("/services/rest", methods=["GET", "POST"])
    def rest():
        if not _authorized(REAL["api"] + "/services/rest", list(request.form.items())):
            return fail(98, "Invalid auth token")
        args = {**request.args, **request.form}
        name = args.get("method")
        with state.lock:
            state.bump("calls")
            if name == "flickr.test.login":
                return jsonify({"user": {"id": USER["id"], "username": {"_content": USER["username"]}}, "stat": "ok"})
            if name == "flickr.photosets.getList":
                return jsonify({"photosets": {"page": 1, "pages": 1, "photoset": [
                    {"id": sid, "title": {"_content": s["title"]}, "photos": len(s["photos"])}
                    for sid, s in state.sets.items()]}, "stat": "ok"})
            if request.method != "POST" and name not in ("flickr.photosets.getPhotos",):
                return fail(1, f"{name} needs POST")
            if name == "flickr.photosets.create":
                if args.get("primary_photo_id") not in state.photos:
                    return fail(2, "Invalid primary photo id")
                sid = str(72157000000000 + len(state.sets) + 1)
                state.sets[sid] = {"title": args["title"], "photos": [args["primary_photo_id"]]}
                state.bump("sets_created")
                return jsonify({"photoset": {"id": sid, "url": f"https://www.flickr.com/photos/x/sets/{sid}"},
                                "stat": "ok"})
            photoset = state.sets.get(args.get("photoset_id", ""))
            if name == "flickr.photosets.getPhotos":
                if photoset is None:
                    return fail(1, "Photoset not found")
                return jsonify({"photoset": {"id": args["photoset_id"], "page": 1, "pages": 1, "photo": [
                    {"id": pid, "title": state.photos[pid]["title"]} for pid in photoset["photos"]]}, "stat": "ok"})
            if name == "flickr.photosets.addPhoto":
                if photoset is None or args.get("photo_id") not in state.photos:
                    return fail(1, "Photoset or photo not found")
                if args["photo_id"] in photoset["photos"]:
                    return fail(3, "Photo already in set")
                photoset["photos"].append(args["photo_id"])
                return jsonify({"stat": "ok"})
            if name == "flickr.photosets.removePhoto":
                if photoset is None or args.get("photo_id") not in photoset["photos"]:
                    return fail(2, "Photo not in set")
                photoset["photos"].remove(args["photo_id"])
                if not photoset["photos"]:
                    del state.sets[args["photoset_id"]]
                return jsonify({"stat": "ok"})
            if name == "flickr.photos.delete":
                if state.photos.pop(args.get("photo_id", ""), None) is None:
                    return fail(1, "Photo not found")
                state.bump("deleted")
                return jsonify({"stat": "ok"})
        return fail(112, f"Method {name} not found")

    @app.post("/services/upload/")
    def upload():
        if not _authorized(REAL["up"] + "/services/upload/", list(request.form.items())):
            return Response('<rsp stat="fail"><err code="98" msg="Invalid auth token" /></rsp>', mimetype="text/xml")
        photo = request.files.get("photo")
        if photo is None:
            return Response('<rsp stat="fail"><err code="2" msg="No photo specified" /></rsp>', mimetype="text/xml")
        with state.lock:
            pid = str(52000000000 + state.counters.get("uploads", 0) + 1)   # never reused, even after deletes
            state.photos[pid] = {"title": request.form.get("title", photo.filename), "size": len(photo.read())}
            state.bump("uploads")
        return Response(f'<?xml version="1.0" encoding="utf-8" ?>\n<rsp stat="ok">\n<photoid>{pid}</photoid>\n</rsp>',
                        mimetype="text/xml")

    @app.get("/_admin/state")
    def admin_state():
        with state.lock:
            return jsonify({"counters": state.counters, "photos": list(state.photos),
                            "sets": {s["title"]: s["photos"] for s in state.sets.values()}})

    @app.post("/_admin/revoke")
    def admin_revoke():
        with state.lock:
            state.access_tokens.clear()
        return jsonify({"ok": True})

    return app


def overrides(base_url: str) -> str:
    """The YAFFO_SYNC_ORIGIN_OVERRIDES value pointing Yaffo at this fake."""
    return json.dumps({origin: base_url for origin in REAL.values()})


class FakeFlickr:
    """The fake on a background thread, for tests."""

    def __init__(self):
        logging.getLogger("waitress").setLevel(logging.ERROR)
        self.state = State()
        self.server = create_server(create_app(self.state), host="127.0.0.1", port=0, threads=8)
        self.base_url = f"http://127.0.0.1:{self.server.effective_port}"
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        try:
            self.server.run()
        except (OSError, ValueError):
            pass

    def __enter__(self) -> FakeFlickr:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.close()

    @property
    def overrides(self) -> str:
        return overrides(self.base_url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=5096)
    args = parser.parse_args()
    print(f"export YAFFO_SYNC_ORIGIN_OVERRIDES='{overrides(f'http://127.0.0.1:{args.port}')}'", flush=True)
    print(f"App key {CONSUMER_KEY}, secret {CONSUMER_SECRET}", flush=True)
    create_app(State()).run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()

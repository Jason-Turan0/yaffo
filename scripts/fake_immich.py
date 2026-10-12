#!/usr/bin/env python3
"""A fake Immich server for tests and the UI sandbox.

It answers the calls Yaffo's Immich presets and its key sign-in make, with the
shapes of Immich's REST API (v1.1xx), over plain http on one local port. That's a
private address, so it also exercises Yaffo's "http on a home network" rule:

- `GET /api/users/me`: the key's user (the sign-in's `check` call);
- albums: `GET /api/albums`, `GET /api/albums/{id}`, `POST /api/albums`,
  `PUT /api/albums/{id}/assets` and `DELETE /api/albums/{id}/assets`;
- assets: `POST /api/search/metadata` (paged with `nextPage`, filtered by
  `albumIds`), `GET /api/assets/{id}/original`, `POST /api/assets` (multipart
  upload; a file whose checksum is already there answers `duplicate` with the
  existing id, as Immich does), and `DELETE /api/assets` (to the trash);
- `/_admin/*`: add photos, revoke the key, and read the state.

Every `/api/*` call needs the `x-api-key` header. Run (one line):
    python -m scripts.fake_immich --port 5097 --photos 40
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import logging
import secrets
import threading
from dataclasses import dataclass, field

from flask import Flask, Response, abort, jsonify, request
from waitress.server import create_server

from scripts.fake_onedrive import jpeg_bytes

API_KEY = "immich-test-key"
USER = {"id": "user-1", "email": "jason@example.com", "name": "Jason Example"}


@dataclass
class Asset:
    id: str
    name: str
    content: bytes
    created: str
    mime: str = "image/jpeg"
    trashed: bool = False
    uploaded: bool = False
    location: dict | None = None

    @property
    def checksum(self) -> str:
        return base64.b64encode(hashlib.sha1(self.content).digest()).decode("ascii")

    def to_json(self) -> dict:
        exif = {"fileSizeInByte": len(self.content), "dateTimeOriginal": self.created,
                "latitude": (self.location or {}).get("lat"), "longitude": (self.location or {}).get("lon")}
        return {"id": self.id, "type": "VIDEO" if self.mime.startswith("video/") else "IMAGE",
                "originalFileName": self.name, "originalMimeType": self.mime, "fileCreatedAt": self.created,
                "fileModifiedAt": self.created, "localDateTime": self.created, "updatedAt": self.created,
                "checksum": self.checksum, "isTrashed": self.trashed, "exifInfo": exif}


@dataclass
class Album:
    id: str
    name: str
    asset_ids: list[str] = field(default_factory=list)


@dataclass
class State:
    keys: set[str] = field(default_factory=lambda: {API_KEY})
    assets: dict[str, Asset] = field(default_factory=dict)
    albums: dict[str, Album] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def bump(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1

    def add_photos(self, count: int, album: str | None = None, prefix: str = "IMG") -> list[str]:
        ids = []
        for _ in range(count):
            seed = len(self.assets) + 1
            asset = Asset(id=f"asset-{seed:05d}", name=f"{prefix}_{seed:04d}.jpg",
                          content=jpeg_bytes(seed, with_exif=True),
                          created=f"2023-{1 + seed % 12:02d}-{1 + seed % 27:02d}T10:{seed % 60:02d}:00.000Z")
            self.assets[asset.id] = asset
            ids.append(asset.id)
            if album:
                self.album_named(album).asset_ids.append(asset.id)
        return ids

    def album_named(self, name: str) -> Album:
        found = next((a for a in self.albums.values() if a.name == name), None)
        if found is None:
            found = Album(id=f"album-{len(self.albums) + 1}", name=name)
            self.albums[found.id] = found
        return found


def create_app(state: State) -> Flask:
    app = Flask("fake_immich")

    @app.before_request
    def guard() -> Response | None:
        if not request.path.startswith("/api/"):
            return None
        if request.headers.get("x-api-key") not in state.keys:
            return jsonify({"message": "Invalid API key", "statusCode": 401}), 401
        with state.lock:
            state.bump("calls")
        return None

    def album_json(album: Album, with_assets: bool) -> dict:
        body = {"id": album.id, "albumName": album.name, "assetCount": len(album.asset_ids)}
        if with_assets:
            body["assets"] = [state.assets[i].to_json() for i in album.asset_ids if not state.assets[i].trashed]
        return body

    @app.get("/api/users/me")
    def me():
        return jsonify(USER)

    @app.get("/api/albums")
    def albums():
        with state.lock:
            return jsonify([album_json(a, False) for a in state.albums.values()])

    @app.get("/api/albums/<album_id>")
    def album(album_id: str):
        with state.lock:
            found = state.albums.get(album_id) or abort(404)
            return jsonify(album_json(found, request.args.get("withoutAssets") != "true"))

    @app.post("/api/albums")
    def create_album():
        body = request.get_json(force=True)
        with state.lock:
            created = Album(id=f"album-{secrets.token_hex(4)}", name=body["albumName"],
                            asset_ids=list(body.get("assetIds") or []))
            state.albums[created.id] = created
            state.bump("albums_created")
            return jsonify(album_json(created, False)), 201

    @app.put("/api/albums/<album_id>/assets")
    def add_to_album(album_id: str):
        ids = request.get_json(force=True).get("ids") or []
        with state.lock:
            found = state.albums.get(album_id) or abort(404)
            results = []
            for asset_id in ids:
                if asset_id not in state.assets:
                    results.append({"id": asset_id, "success": False, "error": "not_found"})
                elif asset_id in found.asset_ids:
                    results.append({"id": asset_id, "success": False, "error": "duplicate"})
                else:
                    found.asset_ids.append(asset_id)
                    results.append({"id": asset_id, "success": True})
            return jsonify(results)

    @app.delete("/api/albums/<album_id>/assets")
    def remove_from_album(album_id: str):
        ids = request.get_json(force=True).get("ids") or []
        with state.lock:
            found = state.albums.get(album_id) or abort(404)
            results = []
            for asset_id in ids:
                if asset_id in found.asset_ids:
                    found.asset_ids.remove(asset_id)
                    results.append({"id": asset_id, "success": True})
                else:
                    results.append({"id": asset_id, "success": False, "error": "not_found"})
            return jsonify(results)

    @app.post("/api/search/metadata")
    def search():
        body = request.get_json(force=True) or {}
        page, size = int(body.get("page") or 1), min(int(body.get("size") or 250), 1000)
        with state.lock:
            assets = [a for a in state.assets.values() if not a.trashed]
            album_ids = body.get("albumIds") or []
            if album_ids:
                members = {i for album_id in album_ids for i in state.albums.get(album_id, Album("", "")).asset_ids}
                assets = [a for a in assets if a.id in members]
            assets.sort(key=lambda a: (a.created, a.id), reverse=body.get("order") != "asc")
            chunk = assets[(page - 1) * size:page * size]
            more = page * size < len(assets)
            return jsonify({"albums": {"total": 0, "count": 0, "items": [], "nextPage": None},
                            "assets": {"total": len(assets), "count": len(chunk),
                                       "items": [a.to_json() for a in chunk],
                                       "nextPage": str(page + 1) if more else None}})

    @app.get("/api/assets/<asset_id>/original")
    def original(asset_id: str):
        with state.lock:
            asset = state.assets.get(asset_id)
            if asset is None or asset.trashed:
                abort(404)
            state.bump("downloads")
            return Response(asset.content, mimetype=asset.mime)

    @app.post("/api/assets")
    def upload():
        uploaded = request.files.get("assetData")
        if uploaded is None or not request.form.get("deviceAssetId") or not request.form.get("fileCreatedAt"):
            return jsonify({"message": "assetData, deviceAssetId and fileCreatedAt are required"}), 400
        content = uploaded.read()
        with state.lock:
            state.bump("uploads")
            checksum = base64.b64encode(hashlib.sha1(content).digest()).decode("ascii")
            existing = next((a for a in state.assets.values() if a.checksum == checksum and not a.trashed), None)
            if existing is not None:
                return jsonify({"id": existing.id, "status": "duplicate"}), 200
            asset = Asset(id=f"asset-up-{secrets.token_hex(4)}", name=uploaded.filename or "upload.jpg",
                          content=content, created=request.form["fileCreatedAt"],
                          mime=uploaded.mimetype or "image/jpeg", uploaded=True)
            state.assets[asset.id] = asset
            return jsonify({"id": asset.id, "status": "created"}), 201

    @app.delete("/api/assets")
    def delete_assets():
        ids = request.get_json(force=True).get("ids") or []
        with state.lock:
            for asset_id in ids:
                if asset_id in state.assets:
                    state.assets[asset_id].trashed = True
                    state.bump("trashed")
        return Response(status=204)

    @app.post("/_admin/add")
    def admin_add():
        body = request.get_json(force=True) or {}
        with state.lock:
            return jsonify({"ids": state.add_photos(int(body.get("count", 1)), body.get("album"))})

    @app.post("/_admin/revoke")
    def admin_revoke():
        with state.lock:
            state.keys.clear()
        return jsonify({"ok": True})

    @app.get("/_admin/state")
    def admin_state():
        with state.lock:
            return jsonify({"counters": state.counters, "assets": len(state.assets),
                            "albums": {a.name: list(a.asset_ids) for a in state.albums.values()}})

    return app


class FakeImmich:
    """The fake on a background thread, for tests."""

    def __init__(self, photos: int = 0, album: str | None = None):
        logging.getLogger("waitress").setLevel(logging.ERROR)
        self.state = State()
        if photos:
            self.state.add_photos(photos, album)
        self.server = create_server(create_app(self.state), host="127.0.0.1", port=0, threads=8)
        self.base_url = f"http://127.0.0.1:{self.server.effective_port}"
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        try:
            self.server.run()
        except (OSError, ValueError):
            pass

    def __enter__(self) -> FakeImmich:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=5097)
    parser.add_argument("--photos", type=int, default=40)
    parser.add_argument("--album", default="Family")
    args = parser.parse_args()
    state = State()
    state.add_photos(args.photos, args.album)
    print(f"Immich at http://127.0.0.1:{args.port}, API key {API_KEY}", flush=True)
    create_app(state).run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()

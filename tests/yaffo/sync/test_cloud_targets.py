"""Cloud target hooks and the host's upload boundary."""
from dataclasses import replace
from pathlib import Path
from io import BytesIO

import pytest
import requests

from yaffo.sync.adapter_static import analyze_adapter
from yaffo.sync import access, setup
from yaffo.sync.adapters import load_adapter
from yaffo.sync.preset_seed import ensure_presets
from yaffo.sync.schemas import Selection
from yaffo.sync.target_run import run_target
from yaffo.db.models import SyncAdapter, SYNC_ITEM_PENDING, SYNC_ITEM_SENT
from yaffo.db.repositories import sync_repository
from yaffo.sync.providers import ONEDRIVE
from yaffo.sync.schemas import GRANT_ENDPOINT, GRANT_FOLLOWUP, WebGrant
from yaffo.sync.starlark_adapter import HookCall
from yaffo.sync.web import CONNECTION_TOKEN, WebClient

pytestmark = pytest.mark.unit

PRESETS = Path(__file__).parents[3] / "yaffo" / "sync" / "presets"


def _grant(method, pattern, kind=GRANT_ENDPOINT):
    return WebGrant(method=method, url_pattern=pattern, grant_kind=kind, followup_of=None,
                    risk="write", granted_code_hash="test", granted_at="2026-10-10T00:00:00Z")


class RecordingHTTP:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        data = kwargs.get("data")
        if hasattr(data, "read"):
            data = data.read()
        files = kwargs.get("files")
        file_bytes = {field: entry[1].read() for field, entry in files.items()} if files else None
        self.calls.append((method, url, kwargs.get("headers"), data, file_bytes))
        response = requests.Response()
        response.status_code = 201
        response.raw = BytesIO(b'{"id":"remote-1"}')
        response.url = url
        response.headers["content-type"] = "application/json"
        return response


def test_upload_host_sends_only_prepared_item_and_bounded_chunk(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"abcdefghij")
    http = RecordingHTTP()
    web = WebClient(provider=ONEDRIVE, grants=[_grant("PUT", None, GRANT_FOLLOWUP)],
                    code_hash="test", tokens=None, http=http)
    url = "https://files.up.1drv.com/upload/once"
    web._seen.add(url)
    web.upload_paths = {7: photo}

    assert web.web_upload_media(CONNECTION_TOKEN, "PUT", url, 8)["status"] == 0
    assert web.web_upload_media(CONNECTION_TOKEN, "PUT", "https://evil.example/x", 7)["status"] == 0
    assert web.web_upload_media(CONNECTION_TOKEN, "PUT", url, 7, offset=1, length=4,
                                headers={"Content-Range": "bytes 1-4/10"})["status"] == 201
    assert http.calls[0][3] == b"bcde"
    assert http.calls[0][2]["Content-Range"] == "bytes 1-4/10"
    assert "Authorization" not in http.calls[0][2]


def test_upload_host_supports_multipart_without_exposing_a_path(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo bytes")
    http = RecordingHTTP()
    url = "https://graph.microsoft.com/v1.0/upload"
    web = WebClient(provider=ONEDRIVE, grants=[_grant("POST", url)],
                    code_hash="test", tokens=None, http=http, upload_paths={7: photo})

    result = web.web_upload_media(CONNECTION_TOKEN, "POST", url, 7,
                                  encoding="multipart", field="image", fields={"caption": "hello"})

    assert result["status"] == 201
    assert http.calls[0][3] == {"caption": "hello"}
    assert http.calls[0][4] == {"image": b"photo bytes"}


class GoogleService:
    def __init__(self):
        self.albums = {}
        self.media = []
        self.uploads = 0
        self.upload_paths = {}
        self.lose_create_reply = False
        self.auth_failed = False

    def boundary(self):
        return None

    def web_request(self, _connection, method, url, **kwargs):
        if url.endswith("/albums") and method == "GET":
            return {"status": 200, "json": {"albums": [
                {"id": album_id, "title": title} for title, album_id in self.albums.items()]}}
        if url.endswith("/albums") and method == "POST":
            title = kwargs["json"]["album"]["title"]
            self.albums[title] = "album-1"
            return {"status": 200, "json": {"id": "album-1"}}
        if url.endswith("/mediaItems:batchCreate"):
            entries = kwargs["json"]["newMediaItems"]
            for entry in entries:
                self.media.append({"id": f"media-{len(self.media) + 1}",
                                   "filename": entry["simpleMediaItem"]["fileName"]})
            if self.lose_create_reply:
                self.lose_create_reply = False
                return {"status": 0, "error": "response lost"}
            return {"status": 200, "json": {"newMediaItemResults": [
                {"mediaItem": {"id": media["id"]}} for media in self.media[-len(entries):]]}}
        if url.endswith("/mediaItems:search"):
            return {"status": 200, "json": {"mediaItems": self.media}}
        raise AssertionError((method, url))

    def web_upload_media(self, *_args, **_kwargs):
        self.uploads += 1
        return {"status": 200, "text": f"token-{self.uploads}"}


def test_google_target_finds_completed_upload_after_state_was_lost():
    code = (PRESETS / "google_photos_target.star").read_text()
    hooks = analyze_adapter(code).metadata["hooks"]
    service = GoogleService()
    call = HookCall(code, service)
    item = {"key": "42", "media_item_id": 7, "target": "Family", "caption": "",
            "filename": "photo__yaffo_42.jpg", "mime": "image/jpeg", "size": 10}
    sent = call.call("push", hooks, [item], {"options": {}, "state": {}, "run": {}})
    assert sent["results"] == [{"key": "42", "remote_id": "media-1"}]
    # A killed worker lost state_patch and results, but the remote album and
    # media item exist. Reconcile discovers both from the pending row's target.
    found = call.call("list_remote", hooks, None, {"options": {}, "state": {}, "run": {},
                                                    "reconcile_items": [{"target": "Family"}]})
    assert found["items"] == [{"remote_id": "media-1", "target": "Family",
                               "filename": "photo__yaffo_42.jpg"}]


def test_google_target_run_recovers_pending_upload_without_duplicate(
        session, add_photo, album, run_context, monkeypatch):
    photo = add_photo("photo.jpg")
    family = album("Family", [photo])
    ensure_presets(session)
    adapter_row = session.query(SyncAdapter).filter_by(slug="preset.google_photos_target").one()
    automation = setup.create_sync(session, "Send family", adapter_id=adapter_row.id)
    setup.set_selection(session, automation, Selection(kind="album", album_id=family.id))
    adapter = load_adapter(session, adapter_row.id)
    service = GoogleService()

    def attach(_session, loaded, _config, **_kwargs):
        loaded.handler.attach(service)
        return service

    monkeypatch.setattr(access, "attach", attach)
    column = sync_repository.load_sync(automation)
    first = run_target(session, automation, column, adapter, run_context)
    assert first.params["sent"] == 1 and service.uploads == 1
    row = sync_repository.rows(session, automation.id)[0]
    assert row.status == SYNC_ITEM_SENT

    # Recreate the durable intent after a worker died between Google's
    # batchCreate response and Yaffo's result transaction.
    row.status, row.remote_id = SYNC_ITEM_PENDING, None
    row.details = {"filename": f"photo__yaffo_{row.id}.jpg"}
    runtime = sync_repository.load_sync(automation).runtime
    sync_repository.save_runtime(automation, replace(runtime, state={}))
    session.commit()

    recovered = run_target(session, automation, sync_repository.load_sync(automation), adapter, run_context)
    assert recovered.params["sent"] == 0
    assert service.uploads == 1
    row = sync_repository.rows(session, automation.id)[0]
    assert row.status == SYNC_ITEM_SENT and row.remote_id == "media-1"
    assert sync_repository.load_sync(automation).runtime.state["albums"] == {"Family": "album-1"}


def test_google_target_reconciles_an_uncertain_batch_response(
        session, add_photo, album, run_context, monkeypatch):
    photo = add_photo("photo.jpg")
    family = album("Family", [photo])
    ensure_presets(session)
    adapter_row = session.query(SyncAdapter).filter_by(slug="preset.google_photos_target").one()
    automation = setup.create_sync(session, "Send family", adapter_id=adapter_row.id)
    setup.set_selection(session, automation, Selection(kind="album", album_id=family.id))
    adapter = load_adapter(session, adapter_row.id)
    service = GoogleService()
    service.lose_create_reply = True

    def attach(_session, loaded, _config, **_kwargs):
        loaded.handler.attach(service)
        return service

    monkeypatch.setattr(access, "attach", attach)
    first = run_target(session, automation, sync_repository.load_sync(automation), adapter, run_context)
    assert first.params["sent"] == 0
    assert sync_repository.rows(session, automation.id)[0].status == SYNC_ITEM_PENDING
    assert len(service.media) == 1

    second = run_target(session, automation, sync_repository.load_sync(automation), adapter, run_context)
    assert second.params["sent"] == 0
    assert sync_repository.rows(session, automation.id)[0].status == SYNC_ITEM_SENT
    assert len(service.media) == 1 and service.uploads == 1


class OneDriveService:
    def __init__(self):
        self.slices = []
        self.delete_headers = []

    def web_request(self, _connection, method, url, **kwargs):
        if url.endswith("/children") and method == "GET":
            return {"status": 200, "json": {"value": []}}
        if url.endswith("/children") and method == "POST":
            return {"status": 201, "json": {"id": "dir-1"}}
        if url.endswith("/createUploadSession"):
            return {"status": 200, "json": {"uploadUrl": "https://files.up.1drv.com/upload/once"}}
        if method == "DELETE":
            self.delete_headers.append(kwargs.get("headers"))
            return {"status": 412, "json": {}}
        raise AssertionError((method, url))

    def web_upload_media(self, _connection, _method, _url, _media_id, **kwargs):
        self.slices.append((kwargs["offset"], kwargs["length"], kwargs["headers"]["Content-Range"]))
        return {"status": 202 if len(self.slices) == 1 else 201,
                "json": {} if len(self.slices) == 1 else {"id": "drive-1", "eTag": "etag-1"}}


def test_onedrive_target_chunks_session_upload():
    code = (PRESETS / "onedrive_target.star").read_text()
    hooks = analyze_adapter(code).metadata["hooks"]
    service = OneDriveService()
    item = {"key": "42", "media_item_id": 7, "target": "Family", "caption": None,
            "filename": "photo__yaffo_42.jpg", "mime": "image/jpeg", "size": 1_638_500}
    sent = HookCall(code, service).call("push", hooks, [item],
                                       {"options": {"folder": {"id": "root"}}, "state": {}, "run": {}})
    assert sent["results"] == [{"key": "42", "remote_id": "drive-1", "details": {"etag": "etag-1"}}]
    assert service.slices == [(0, 1_638_400, "bytes 0-1638399/1638500"),
                              (1_638_400, 100, "bytes 1638400-1638499/1638500")]


def test_onedrive_target_keeps_a_copy_changed_after_upload():
    code = (PRESETS / "onedrive_target.star").read_text()
    hooks = analyze_adapter(code).metadata["hooks"]
    service = OneDriveService()
    item = {"key": "42", "remote_id": "drive-1", "target": "Family", "media_item_id": 7,
            "details": {"etag": "original"}}

    removed = HookCall(code, service).call("remove", hooks, [item],
                                          {"options": {"folder": {"id": "root"}}, "state": {}, "run": {}})

    assert removed["results"] == [{"key": "42", "error": {"code": "changed"}}]
    assert service.delete_headers == [{"If-Match": "original"}]

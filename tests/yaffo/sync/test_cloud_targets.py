"""Cloud target hooks and the host's upload boundary."""
import json
import os
import subprocess
import sys
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
from yaffo.db.models import AlbumItem, SyncAdapter, SYNC_ITEM_PENDING, SYNC_ITEM_SENT
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
                                   "filename": entry["simpleMediaItem"]["fileName"],
                                   "album_id": kwargs["json"]["albumId"]})
            if self.lose_create_reply:
                self.lose_create_reply = False
                return {"status": 0, "error": "response lost"}
            return {"status": 200, "json": {"newMediaItemResults": [
                {"mediaItem": {"id": media["id"]}} for media in self.media[-len(entries):]]}}
        if url.endswith("/mediaItems:search"):
            return {"status": 200, "json": {"mediaItems": [
                {"id": item["id"], "filename": item["filename"]} for item in self.media
                if item["album_id"] == kwargs["json"]["albumId"]]}}
        if url.endswith(":batchRemoveMediaItems"):
            ids = set(kwargs["json"]["mediaItemIds"])
            for item in self.media:
                if item["id"] in ids:
                    item["album_id"] = None
            return {"status": 200}
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
    assert sent["results"] == [{"key": "42", "remote_id": "media-1", "details": {"album_id": "album-1"}}]
    # A killed worker lost state_patch and results, but the remote album and
    # media item exist. Reconcile discovers both from the pending row's target.
    found = call.call("list_remote", hooks, None, {"options": {}, "state": {}, "run": {},
                                                    "reconcile_items": [{"target": "Family"}]})
    assert found["items"] == [{"remote_id": "media-1", "target": "Family",
                               "filename": "photo__yaffo_42.jpg", "details": {"album_id": "album-1"}}]


def test_google_target_removes_membership_after_album_state_was_lost():
    code = (PRESETS / "google_photos_target.star").read_text()
    hooks = analyze_adapter(code).metadata["hooks"]
    service = GoogleService()
    call = HookCall(code, service)
    item = {"key": "42", "media_item_id": 7, "target": "Family", "caption": "",
            "filename": "photo__yaffo_42.jpg", "mime": "image/jpeg", "size": 10}
    sent = call.call("push", hooks, [item], {"options": {}, "state": {}, "run": {}})
    assert service.media[0]["album_id"] == "album-1"

    old_row = {"key": "42", "remote_id": sent["results"][0]["remote_id"],
               "target": "Family", "details": {}}
    removed = call.call("remove", hooks, [old_row], {"options": {}, "state": {}, "run": {}})

    assert removed["results"] == [{"key": "42"}]
    assert removed["state_patch"] == {"albums": {"Family": "album-1"}}
    assert service.media[0]["album_id"] is None


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


def test_google_target_removes_old_copy_with_missing_album_state(
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
    run_target(session, automation, sync_repository.load_sync(automation), adapter, run_context)
    row = sync_repository.rows(session, automation.id)[0]
    row.details = None
    runtime = sync_repository.load_sync(automation).runtime
    sync_repository.save_runtime(automation, replace(runtime, state={}, cursors={}))
    session.query(AlbumItem).filter_by(album_id=family.id, media_item_id=photo.id).delete()
    session.commit()

    removed = run_target(session, automation, sync_repository.load_sync(automation), adapter, run_context)

    assert removed.params["removed"] == 1 and removed.params["sent"] == 0
    assert service.media[0]["album_id"] is None and service.uploads == 1


def test_google_target_marks_remote_deletion_without_reupload(
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
    run_target(session, automation, sync_repository.load_sync(automation), adapter, run_context)
    service.media[0]["album_id"] = None
    runtime = sync_repository.load_sync(automation).runtime
    sync_repository.save_runtime(automation, replace(runtime, cursors={}))
    session.commit()

    drift = run_target(session, automation, sync_repository.load_sync(automation), adapter, run_context)

    assert drift.params["removed_remotely"] == 1 and drift.params["sent"] == 0
    assert sync_repository.rows(session, automation.id)[0].status == "removed_remotely"
    assert service.uploads == 1


def test_google_target_survives_worker_exit_after_remote_create(
        session, add_photo, album, run_context, tmp_path, monkeypatch):
    photo = add_photo("photo.jpg")
    family = album("Family", [photo])
    ensure_presets(session)
    adapter_row = session.query(SyncAdapter).filter_by(slug="preset.google_photos_target").one()
    automation = setup.create_sync(session, "Send family", adapter_id=adapter_row.id)
    setup.set_selection(session, automation, Selection(kind="album", album_id=family.id))
    session.commit()
    remote_file = tmp_path / "remote.json"
    child_code = """
import json, os, sys
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from yaffo.db.models import Automation
from yaffo.db.repositories import sync_repository
from yaffo.sync import access
from yaffo.sync.adapters import load_adapter
from yaffo.sync.run_common import RunContext
from yaffo.sync.target_run import run_target

remote_file = Path(sys.argv[2])
class Remote:
    auth_failed = False
    upload_paths = {}
    def boundary(self):
        pass
    def web_upload_media(self, *args, **kwargs):
        return {"status": 200, "text": "upload-token"}
    def web_request(self, connection, method, url, **kwargs):
        if url.endswith("/albums") and method == "GET":
            return {"status": 200, "json": {"albums": [{"id": "album-1", "title": "Family"}]}}
        if url.endswith("/mediaItems:search"):
            items = json.loads(remote_file.read_text()) if remote_file.exists() else []
            return {"status": 200, "json": {"mediaItems": items}}
        if url.endswith("/mediaItems:batchCreate"):
            item = kwargs["json"]["newMediaItems"][0]
            remote_file.write_text(json.dumps([{"id": "media-1", "filename":
                item["simpleMediaItem"]["fileName"]}]))
            os._exit(72)
        raise AssertionError((method, url))

engine = create_engine("sqlite:///" + sys.argv[1])
with Session(engine) as session:
    automation = session.get(Automation, int(sys.argv[3]))
    column = sync_repository.load_sync(automation)
    adapter = load_adapter(session, column.config.adapter_id)
    def attach(_session, loaded, _config, **kwargs):
        remote = Remote()
        loaded.handler.attach(remote)
        return remote
    access.attach = attach
    run_target(session, automation, column, adapter,
               RunContext(scratch_dir=Path(sys.argv[2]).parent))
"""
    env = {**os.environ, "YAFFO_DATA_DIR": str(tmp_path), "PYTHONPATH": str(PRESETS.parents[2])}
    child = subprocess.run([sys.executable, "-c", child_code, str(session.bind.url.database),
                            str(remote_file), str(automation.id)], env=env, capture_output=True,
                           text=True, timeout=30, check=False)
    assert child.returncode == 72, child.stderr
    session.expire_all()
    row = sync_repository.rows(session, automation.id)[0]
    assert row.status == SYNC_ITEM_PENDING and row.remote_id is None
    assert json.loads(remote_file.read_text())[0]["filename"] == row.details["filename"]

    service = GoogleService()
    service.albums["Family"] = "album-1"
    service.media = [{**item, "album_id": "album-1"} for item in json.loads(remote_file.read_text())]

    def attach(_session, loaded, _config, **_kwargs):
        loaded.handler.attach(service)
        return service

    monkeypatch.setattr(access, "attach", attach)
    adapter = load_adapter(session, adapter_row.id)
    recovered = run_target(session, automation, sync_repository.load_sync(automation), adapter, run_context)
    row = sync_repository.rows(session, automation.id)[0]
    assert recovered.params["sent"] == 0 and row.status == SYNC_ITEM_SENT
    assert row.remote_id == "media-1" and service.uploads == 0


class OneDriveService:
    def __init__(self):
        self.slices = []
        self.delete_headers = []
        self.created_folders = []

    def web_request(self, _connection, method, url, **kwargs):
        if url.endswith("/children") and method == "GET":
            return {"status": 200, "json": {"value": []}}
        if url.endswith("/children") and method == "POST":
            self.created_folders.append((url.split("/items/", 1)[1].split("/children", 1)[0],
                                         kwargs["json"]["name"]))
            return {"status": 201, "json": {"id": f"dir-{len(self.created_folders)}"}}
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


@pytest.mark.parametrize(("layout", "target", "expected_folders"), [
    ("flat", ".", []),
    ("per_target", "Family", [("root", "Family")]),
    ("by_date", "2025/02", [("root", "2025"), ("dir-1", "02")]),
])
def test_onedrive_target_layout_creates_the_expected_folder(layout, target, expected_folders):
    code = (PRESETS / "onedrive_target.star").read_text()
    hooks = analyze_adapter(code).metadata["hooks"]
    service = OneDriveService()
    item = {"key": "42", "media_item_id": 7, "target": target, "filename": "photo.jpg",
            "mime": "image/jpeg", "size": 1_638_500}

    sent = HookCall(code, service).call("push", hooks, [item],
                                       {"options": {"folder": {"id": "root"}, "layout": layout},
                                        "state": {}, "run": {}})

    assert sent["results"][0]["remote_id"] == "drive-1"
    assert service.created_folders == expected_folders


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

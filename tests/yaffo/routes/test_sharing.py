"""Route tests for the Sharing tab: sidebar + settings page rendering
(identity, presence tri-state, revoked rows), pairing-code generation, the
accept flow's success/error paths, per-device pages (rename, revoke), and
revocation — all against a stub P2PService so no sockets or keychain are
involved. The real engine is exercised end-to-end through these same routes
in tests/yaffo/p2p/test_service_integration.py."""
import json
from types import SimpleNamespace

import pytest

from yaffo.db import db
from yaffo.db.models import (
    GRANT_SCOPE_FOLDER,
    GRANT_SCOPE_MEDIA_DIR,
    ApplicationSettings,
    KnownDevice,
    ShareGrant,
    TRUST_STATE_REVOKED,
    TRUST_STATE_TRUSTED,
)
from yaffo.db.repositories import media_dir_repository, p2p_repository
from yaffo.p2p.pairing import PairingError, new_pairing_code
from yaffo.p2p.service import P2PServiceError
from yaffo.p2p.signaling import CallError
from yaffo.utils.settings import SHARED_DOWNLOAD_DIR_SETTING

pytestmark = pytest.mark.unit

MY_ID = "AAAA-BBBB-CCCC-DDDD"
PEER_ONLINE = "ONLI-NEON-LINE-ONLI"
PEER_OFFLINE = "OFFL-INEO-FFLI-NEOF"
PEER_REVOKED = "REVO-KEDR-EVOK-EDRE"


class FakeP2PService:
    def __init__(self):
        self.identity = SimpleNamespace(device_id=MY_ID, public_key_b64="pubkey")
        self.hub_url = "wss://hub.example"
        self.hub_connected = True
        self.online = {PEER_ONLINE}
        self.local = set()
        self.accept_result = {"peer_device_id": PEER_ONLINE, "via": "relay"}
        self.accept_error = None
        self.revoke_result = {"revoked": PEER_ONLINE, "peer_notified": True}
        self.revoke_error = None
        self.list_shared_result = {
            "status": "ok",
            "type": "shared_list",
            "scopes": [
                {
                    "scope_type": "folder",
                    "media_dir_id": "remote-lib",
                    "relative_path": "trip",
                    "name": "photos",
                    "file_count": 3,
                }
            ],
        }
        self.list_shared_error = None
        self.list_files_result = {
            "status": "ok",
            "type": "shared_files",
            "media_dir_id": "remote-lib",
            "relative_path": "trip",
            "filters": {},
            "offset": 0,
            "limit": 50,
            "total": 3,
            "files": [
                {
                    "media_dir_id": "remote-lib",
                    "relative_path": "trip/a.jpg",
                    "name": "a.jpg",
                    "size": 12,
                    "mtime": 1720000000.0,
                    "media_type": "photo",
                    "date_taken": "2024-03-05T10:00:00",
                    "location_name": "Lisbon",
                    "duration_seconds": None,
                }
            ],
            "facets": {
                "years": [2023, 2024],
                "devices": ["Pixel", "X-T200"],
                "people": [{"id": 7, "name": "Alice"}],
                "labels": [{"id": 3, "name": "beach"}],
                "tag_names": ["event"],
                "locations": ["Lisbon", "Porto"],
                "tag_values": ["holiday"],
            },
        }
        self.list_files_error = None
        self.preview_bytes = b"jpeg-bytes"
        self.preview_error = None
        self.transfer_error = None
        self.transfer_rows = []
        self.accepted_codes = []
        self.revoked_ids = []
        self.listed_ids = []
        self.listed_files_calls = []
        self.preview_calls = []
        self.started_batches = []
        self.cancelled_batches = []
        self.continued_batches = []
        self.deleted_batches = []
        self.peering = self
        self.list_shared = SimpleNamespace(send=self.list_shared)
        self.list_files = SimpleNamespace(send=self.list_shared_files)
        self.pull_preview = SimpleNamespace(send=self.pull_preview)
        self.transfers = SimpleNamespace(
            start_batch=self.start_transfer_batch,
            snapshot=self.transfer_snapshot,
            cancel=self.cancel_transfer,
            allow_relay_overage=self.continue_transfer,
            delete=self.delete_transfer,
        )

    def connected_device_ids(self):
        return self.online

    def local_device_ids(self):
        return self.local

    def generate_pairing_code(self):
        return new_pairing_code(MY_ID, "pubkey")

    def send_accept_pairing_code(self, code_text):
        self.accepted_codes.append(code_text)
        if self.accept_error is not None:
            raise self.accept_error
        return self.accept_result

    def revoke_peer(self, device_id):
        self.revoked_ids.append(device_id)
        if self.revoke_error is not None:
            raise self.revoke_error
        p2p_repository.mark_device_revoked(db.session, device_id)
        return self.revoke_result

    def send_revoke_peer(self, device_id):
        return self.revoke_peer(device_id)

    def list_shared(self, device_id):
        self.listed_ids.append(device_id)
        if self.list_shared_error is not None:
            raise self.list_shared_error
        return self.list_shared_result

    def list_shared_files(self, device_id, media_dir_id, relative_path="", filters=None, offset=0, limit=50):
        self.listed_files_calls.append((device_id, media_dir_id, relative_path, filters, offset, limit))
        if self.list_files_error is not None:
            raise self.list_files_error
        return self.list_files_result

    def pull_preview(self, device_id, media_dir_id, relative_path, max_dimension=512):
        self.preview_calls.append((device_id, media_dir_id, relative_path, max_dimension))
        if self.preview_error is not None:
            raise self.preview_error
        return self.preview_bytes

    def start_transfer_batch(
        self,
        peer_device_id,
        peer_name,
        media_dir_id,
        scope,
        label,
        filters,
        destination_root,
        collection_path,
        files=None,
    ):
        self.started_batches.append(
            {
                "peer_device_id": peer_device_id,
                "peer_name": peer_name,
                "media_dir_id": media_dir_id,
                "scope": scope,
                "label": label,
                "filters": filters,
                "destination_root": destination_root,
                "collection_path": collection_path,
                "files": files,
            }
        )
        if self.transfer_error is not None:
            raise self.transfer_error
        return "batch-1"

    def transfer_snapshot(self, peer_device_id=None):
        return self.transfer_rows

    def cancel_transfer(self, batch_id):
        self.cancelled_batches.append(batch_id)
        return True

    def continue_transfer(self, batch_id):
        self.continued_batches.append(batch_id)
        return True

    def delete_transfer(self, batch_id):
        self.deleted_batches.append(batch_id)
        return True


@pytest.fixture
def service(app):
    fake = FakeP2PService()
    app.extensions["p2p_service"] = fake
    return fake


def _seed_devices(app):
    with app.app_context():
        db.session.add_all(
            [
                KnownDevice(device_id=PEER_ONLINE, pubkey="k1", display_name="laptop",
                            trust_state=TRUST_STATE_TRUSTED),
                KnownDevice(device_id=PEER_OFFLINE, pubkey="k2", display_name="desktop",
                            trust_state=TRUST_STATE_TRUSTED),
                KnownDevice(device_id=PEER_REVOKED, pubkey="k3", display_name="old-phone",
                            trust_state=TRUST_STATE_REVOKED),
            ]
        )
        db.session.commit()


def _notification(resp):
    payload = json.loads(resp.headers["HX-Trigger"])["showNotification"]
    return payload["message"], payload["type"]


def _devices_changed(resp):
    return "sharingDevicesChanged" in json.loads(resp.headers.get("HX-Trigger", "{}"))


# ---- pages & sidebar ---------------------------------------------------------


def test_sharing_index_redirects_to_settings(client):
    resp = client.get("/sharing")
    assert resp.status_code == 302
    assert "/sharing/settings" in resp.headers["Location"]


def test_settings_page_unavailable_without_service(client):
    body = client.get("/sharing/settings").get_data(as_text=True)
    assert "Device sharing is not running" in body


def test_settings_page_shows_identity_and_presence_tristate(app, client, service):
    _seed_devices(app)
    body = client.get("/sharing/settings").get_data(as_text=True)
    assert MY_ID in body
    assert "wss://hub.example" in body
    assert "Show a pairing code" in body
    assert "Paired devices" not in body
    assert "known-device-item" not in body
    assert "Downloaded copies" in body


def test_presence_unknown_when_hub_unreachable(app, client, service):
    _seed_devices(app)
    service.online = None
    body = client.get("/sharing/sidebar").get_data(as_text=True)
    assert "laptop" in body
    assert "Online" not in body


def test_local_presence_badge_for_lan_reachable_peer(app, client, service):
    _seed_devices(app)
    service.local = {PEER_ONLINE}
    body = client.get("/sharing/sidebar").get_data(as_text=True)
    assert "laptop" in body and "Local" in body
    assert "desktop" in body


def test_sidebar_lists_devices_with_selection(app, client, service):
    _seed_devices(app)
    body = client.get(f"/sharing/sidebar?selected={PEER_ONLINE}").get_data(as_text=True)
    assert "laptop" in body and "desktop" in body and "old-phone" in body
    assert "active" in body
    # Sidebar shows devices even when the engine isn't running (plain DB read).
    del client.application.extensions["p2p_service"]
    assert "laptop" in client.get("/sharing/sidebar").get_data(as_text=True)


def test_device_page_renders(app, client, service):
    _seed_devices(app)
    body = client.get(f"/sharing/devices/{PEER_ONLINE}").get_data(as_text=True)
    assert "laptop" in body
    assert PEER_ONLINE in body
    assert "Device Name" in body
    assert "Name on this device" not in body
    assert "Only changes how the device is listed here" not in body
    assert "Add Shared" in body
    assert "Downloaded copies" not in body
    assert "Shared by this device" not in body  # the sidebar's "Shared With Me" owns this


def test_device_page_shows_grant_controls_when_media_dirs_exist(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))
    body = client.get(f"/sharing/devices/{PEER_ONLINE}").get_data(as_text=True)
    assert "Add Shared" in body
    assert "Media Directory" in body
    assert "Folder" in body
    assert "library" in body


def test_sidebar_lists_outbound_shares(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir = media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))
        p2p_repository.create_grant(
            db.session,
            PEER_ONLINE,
            GRANT_SCOPE_MEDIA_DIR,
            media_dir_id=media_dir.id,
        )

    body = client.get("/sharing/sidebar").get_data(as_text=True)

    assert "Shared With Others" in body
    assert "laptop - library" in body
    assert "Revoke" in body


def test_device_page_404_for_unknown(client, service):
    assert client.get("/sharing/devices/NOBODY-HOME").status_code == 404


# ---- pairing -----------------------------------------------------------------


def test_generate_pairing_code_fragment(client, service):
    body = client.post("/sharing/pairing-code").get_data(as_text=True)
    assert "data:image/svg+xml" in body  # the QR
    assert "pairing-code-text" in body
    assert "Expires in" in body


def test_pair_success_rerenders_section_with_toast(client, service):
    resp = client.post("/sharing/pair", data={"code": "some-code"})
    assert resp.status_code == 200
    assert service.accepted_codes == ["some-code"]
    message, type_ = _notification(resp)
    assert PEER_ONLINE in message and type_ == "success"
    assert _devices_changed(resp)  # the sidebar refreshes off this
    assert "devices-section" in resp.get_data(as_text=True)


def test_pair_requires_a_code(client, service):
    resp = client.post("/sharing/pair", data={"code": "  "})
    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "Paste a pairing code" in message and type_ == "error"


@pytest.mark.parametrize(
    "error,expected",
    [
        (PairingError("pairing code expired"), "pairing code expired"),
        (CallError("peer did not answer"), "peer did not answer"),
    ],
)
def test_pair_errors_become_toasts_without_swap(client, service, error, expected):
    service.accept_error = error
    resp = client.post("/sharing/pair", data={"code": "bad"})
    assert resp.status_code == 204  # no swap — the pasted code survives
    message, type_ = _notification(resp)
    assert expected in message and type_ == "error"


# ---- revocation & rename -------------------------------------------------------


def test_revoke_notified_toast(app, client, service):
    _seed_devices(app)
    resp = client.post("/sharing/revoke", data={"device_id": PEER_ONLINE})
    assert resp.status_code == 200
    assert service.revoked_ids == [PEER_ONLINE]
    message, _ = _notification(resp)
    assert "was notified" in message
    assert _devices_changed(resp)


def test_revoke_offline_peer_toast(app, client, service):
    _seed_devices(app)
    service.revoke_result = {"revoked": PEER_OFFLINE, "peer_notified": False}
    resp = client.post("/sharing/revoke", data={"device_id": PEER_OFFLINE})
    message, _ = _notification(resp)
    assert "offline" in message


def test_revoke_unknown_device_is_an_error_toast(client, service):
    service.revoke_error = P2PServiceError("NOBODY is not a known device")
    resp = client.post("/sharing/revoke", data={"device_id": "NOBODY"})
    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "not a known device" in message and type_ == "error"


def test_device_page_revoke_rerenders_panel(app, client, service):
    _seed_devices(app)
    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/revoke")
    assert resp.status_code == 200
    assert service.revoked_ids == [PEER_ONLINE]
    assert "device-panel" in resp.get_data(as_text=True)
    assert "Delete" in resp.get_data(as_text=True)


def test_delete_revoked_device_redirects_to_settings(app, client, service):
    _seed_devices(app)
    resp = client.post(f"/sharing/devices/{PEER_REVOKED}/delete")
    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert message == "Device deleted." and type_ == "success"
    assert resp.headers["HX-Redirect"].endswith("/sharing/settings")
    with app.app_context():
        assert db.session.get(KnownDevice, PEER_REVOKED) is None


def test_delete_trusted_device_requires_revoke(app, client, service):
    _seed_devices(app)
    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/delete")
    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "Revoke" in message and type_ == "error"
    with app.app_context():
        assert db.session.get(KnownDevice, PEER_ONLINE) is not None


def test_rename_device(app, client, service):
    _seed_devices(app)
    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/rename", data={"display_name": "kitchen-mac"})
    assert resp.status_code == 200
    assert "kitchen-mac" in resp.get_data(as_text=True)
    assert _devices_changed(resp)
    with app.app_context():
        assert db.session.get(KnownDevice, PEER_ONLINE).display_name == "kitchen-mac"


def test_rename_requires_a_name(app, client, service):
    _seed_devices(app)
    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/rename", data={"display_name": "  "})
    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "cannot be empty" in message and type_ == "error"
    with app.app_context():
        assert db.session.get(KnownDevice, PEER_ONLINE).display_name == "laptop"


# ---- share grants ------------------------------------------------------------


def test_add_media_dir_grant(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir = media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/grants",
        data={"scope_type": GRANT_SCOPE_MEDIA_DIR, "media_dir_id": media_dir.id},
    )

    assert resp.status_code == 200
    message, type_ = _notification(resp)
    assert message == "Share grant added." and type_ == "success"
    with app.app_context():
        grants = p2p_repository.list_active_grants(db.session, PEER_ONLINE)
        assert len(grants) == 1
        assert grants[0].scope_type == GRANT_SCOPE_MEDIA_DIR
        assert grants[0].media_dir_id == media_dir.id


def test_add_folder_grant_stores_relative_path(app, client, service, tmp_path):
    _seed_devices(app)
    root = tmp_path / "library"
    folder = root / "2024" / "summer"
    folder.mkdir(parents=True)
    with app.app_context():
        media_dir = media_dir_repository.add_media_dir(db.session, str(root))

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/grants",
        data={"scope_type": GRANT_SCOPE_FOLDER, "folder_path": str(folder)},
    )

    assert resp.status_code == 200
    with app.app_context():
        grant = db.session.query(ShareGrant).one()
        assert grant.scope_type == GRANT_SCOPE_FOLDER
        assert grant.media_dir_id == media_dir.id
        assert grant.relative_path == "2024/summer"


def test_add_folder_grant_rejects_paths_outside_media_dirs(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/grants",
        data={"scope_type": GRANT_SCOPE_FOLDER, "folder_path": str(tmp_path / "outside")},
    )

    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "inside a configured media directory" in message and type_ == "error"
    with app.app_context():
        assert p2p_repository.list_active_grants(db.session, PEER_ONLINE) == []


def test_sidebar_revoke_share_grant(app, client, service, tmp_path):
    _seed_devices(app)
    with app.app_context():
        media_dir = media_dir_repository.add_media_dir(db.session, str(tmp_path / "library"))
        grant = p2p_repository.create_grant(
            db.session,
            PEER_ONLINE,
            GRANT_SCOPE_MEDIA_DIR,
            media_dir_id=media_dir.id,
        )
        grant_id = grant.id

    resp = client.post(f"/sharing/grants/{grant_id}/revoke?selected={PEER_ONLINE}")

    assert resp.status_code == 200
    message, type_ = _notification(resp)
    assert message == "Share grant revoked." and type_ == "success"
    assert _devices_changed(resp)
    body = resp.get_data(as_text=True)
    assert "sharing-sidebar" in body
    assert "laptop - library" not in body
    with app.app_context():
        assert p2p_repository.list_active_grants(db.session, PEER_ONLINE) == []


# ---- receiving shared files --------------------------------------------------


def test_device_page_loads_remote_panel_on_load(app, client, service):
    """The panel is a placeholder that fetches itself — the p2p call must
    never block the page render."""
    _seed_devices(app)
    body = client.get(f"/sharing/devices/{PEER_ONLINE}").get_data(as_text=True)
    assert "Loading shared folders" in body
    assert 'hx-trigger="load"' in body
    assert service.listed_ids == []


def test_sidebar_shared_with_me_loads_remote_shares(app, client, service):
    _seed_devices(app)

    resp = client.get("/sharing/sidebar/shared-with-me")

    assert resp.status_code == 200
    assert service.listed_ids == [PEER_ONLINE, PEER_OFFLINE]
    body = resp.get_data(as_text=True)
    assert "Shared With Me" in body
    assert "laptop - photos / trip" in body
    assert "desktop - photos / trip" in body
    assert "Open" in body


def test_shared_files_gallery_renders_with_filters_and_facets(app, client, service):
    """The remote gallery is server-rendered like the home page: one
    list_files call per GET carrying the parsed filters, peer facets in the
    year/device selects, lazy preview images, and per-card pull forms."""
    _seed_devices(app)
    service.list_files_result["total"] = 120

    resp = client.get(
        f"/sharing/devices/{PEER_ONLINE}/files"
        "?media_dir_id=remote-lib&scope=trip&label=photos%20/%20trip"
        "&path=beach&media-type=photo&year=2024&month=3&device=Pixel&page=2"
    )

    assert resp.status_code == 200
    assert service.listed_files_calls == [
        (
            PEER_ONLINE,
            "remote-lib",
            "trip",
            {"path": "beach", "media_type": "photo", "year": 2024, "month": 3, "device": "Pixel"},
            50,
            50,
        )
    ]
    body = resp.get_data(as_text=True)
    assert "Shared by laptop" in body
    assert "photos / trip" in body
    # The grid: previews load through the local proxy via the client-side
    # queue (bounded concurrency), never as plain eager/lazy img src.
    assert f"/sharing/devices/{PEER_ONLINE}/preview?" in body
    assert "data-preview-src" in body
    assert "remote_gallery.js" in body
    # Loading shows the skeleton plate; the "not found" placeholder is only
    # the failure fallback, never the initial state.
    assert "preview-pending" in body
    assert "remote-preview-spinner" in body
    assert "data-fallback-src" in body
    assert "trip/a.jpg" in body
    assert "Lisbon" in body
    assert "Pull" in body
    assert 'name="scope" value="trip"' in body
    assert 'name="collection_name" value="trip"' in body
    assert "pull-destination" not in body
    assert "Set a download directory" in body
    # Facets from the peer populate the sidebar selects; selections stick.
    assert "2023" in body and "X-T200" in body
    assert 'value="beach"' in body
    assert "Page 2 of 3" in body


def test_shared_files_page_without_scope_redirects_to_device(app, client, service):
    _seed_devices(app)
    resp = client.get(f"/sharing/devices/{PEER_ONLINE}/files")
    assert resp.status_code == 302
    assert f"/sharing/devices/{PEER_ONLINE}" in resp.headers["Location"]


def test_shared_files_gallery_error_renders_inline(app, client, service):
    _seed_devices(app)
    service.list_files_error = CallError("peer did not answer")

    resp = client.get(f"/sharing/devices/{PEER_ONLINE}/files?media_dir_id=remote-lib&scope=trip")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "peer did not answer" in body
    assert "Try again" in body


def test_shared_files_gallery_revoked_device(app, client, service):
    _seed_devices(app)
    resp = client.get(f"/sharing/devices/{PEER_REVOKED}/files?media_dir_id=remote-lib")
    assert "revoked" in resp.get_data(as_text=True)
    assert service.listed_files_calls == []


def test_preview_proxies_peer_image_with_caching(app, client, service):
    _seed_devices(app)

    resp = client.get(f"/sharing/devices/{PEER_ONLINE}/preview?media_dir_id=remote-lib&path=trip/a.jpg")

    assert resp.status_code == 200
    assert resp.data == b"jpeg-bytes"
    assert resp.headers["Content-Type"] == "image/jpeg"
    assert "max-age" in resp.headers["Cache-Control"]
    assert service.preview_calls == [(PEER_ONLINE, "remote-lib", "trip/a.jpg", 512)]


def test_preview_failure_is_an_error_status_for_the_img_fallback(app, client, service):
    _seed_devices(app)
    service.preview_error = CallError("peer did not answer")
    resp = client.get(f"/sharing/devices/{PEER_ONLINE}/preview?media_dir_id=remote-lib&path=trip/a.jpg")
    assert resp.status_code == 502


def test_save_shared_download_directory(app, client, service, tmp_path):
    _seed_devices(app)
    download_dir = tmp_path / "shared-downloads"

    resp = client.post("/sharing/download-directory", data={"download_dir": str(download_dir)})

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "remote-download-directory-panel" in body
    assert str(download_dir.resolve()) in body
    message, type_ = _notification(resp)
    assert message == "Download directory saved." and type_ == "success"
    assert download_dir.is_dir()
    with app.app_context():
        setting = db.session.query(ApplicationSettings).filter_by(name=SHARED_DOWNLOAD_DIR_SETTING).one()
        assert setting.value == str(download_dir.resolve())


def test_save_shared_download_directory_rejects_file(app, client, service, tmp_path):
    _seed_devices(app)
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("x")

    resp = client.post("/sharing/download-directory", data={"download_dir": str(file_path)})

    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "not a file" in message and type_ == "error"


def _set_download_dir(app, download_dir):
    with app.app_context():
        db.session.add(
            ApplicationSettings(
                name=SHARED_DOWNLOAD_DIR_SETTING,
                type="string",
                value=str(download_dir),
            )
        )
        db.session.commit()


def test_pull_remote_file_queues_a_single_file_batch(app, client, service, tmp_path):
    """The per-card Pull enqueues a background transfer (Phase 6) instead of
    blocking the request on the download; the manifest's size/mtime ride
    along to seed the resume sidecar."""
    _seed_devices(app)
    download_dir = tmp_path / "downloads"
    _set_download_dir(app, download_dir)

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/pull",
        data={
            "remote_media_dir_id": "remote-lib",
            "relative_path": "trip/a.jpg",
            "scope": "trip",
            "collection_name": "trip",
            "name": "a.jpg",
            "size": "12",
            "mtime": "1720000000.0",
        },
    )

    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "a.jpg" in message and "queued" in message and type_ == "success"
    assert "sharingTransfersChanged" in json.loads(resp.headers["HX-Trigger"])
    assert service.started_batches == [
        {
            "peer_device_id": PEER_ONLINE,
            "peer_name": "laptop",
            "media_dir_id": "remote-lib",
            "scope": "trip",
            "label": "a.jpg",
            "filters": {},
            "destination_root": download_dir.resolve(),
            "collection_path": "trip",
            "files": [{"relative_path": "trip/a.jpg", "name": "a.jpg", "size": 12, "mtime": 1720000000.0}],
        }
    ]


def test_download_all_queues_a_batch_with_the_current_filters(app, client, service, tmp_path):
    """Download-all snapshots the whole filtered scope as one batch — the
    filters ride the querystring exactly as the gallery page shows them."""
    _seed_devices(app)
    download_dir = tmp_path / "downloads"
    _set_download_dir(app, download_dir)

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/transfers/download-all"
        "?media_dir_id=remote-lib&scope=trip&label=photos&media-type=photo&year=2024"
    )

    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "queued" in message and type_ == "success"
    assert "sharingTransfersChanged" in json.loads(resp.headers["HX-Trigger"])
    assert len(service.started_batches) == 1
    batch = service.started_batches[0]
    assert batch["peer_device_id"] == PEER_ONLINE
    assert batch["media_dir_id"] == "remote-lib"
    assert batch["scope"] == "trip"
    assert batch["label"] == "photos"
    assert batch["filters"] == {"media_type": "photo", "year": 2024}
    assert batch["collection_path"] == "trip"
    assert batch["files"] is None


def test_download_all_requires_a_scope(app, client, service, tmp_path):
    _seed_devices(app)
    _set_download_dir(app, tmp_path / "downloads")
    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/transfers/download-all")
    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "scope is missing" in message and type_ == "error"
    assert service.started_batches == []


def test_transfers_fragment_renders_batches_and_polls_while_active(app, client, service):
    _seed_devices(app)
    service.transfer_rows = [
        {
            "id": "batch-1",
            "peer_device_id": PEER_ONLINE,
            "peer_name": "laptop",
            "label": "trip",
            "state": "running",
            "path": "relay",
            "active": True,
            "paused_for_budget": False,
            "files_total": 4,
            "files_done": 1,
            "files_failed": 0,
            "bytes_total": 400,
            "bytes_done": 100,
            "relay_bytes": 100,
            "relay_budget_bytes": 2**30,
            "active_files": ["b.jpg"],
            "failed_files": [],
            "error": None,
            "created_at": None,
            "finished_at": None,
        }
    ]

    resp = client.get(f"/sharing/devices/{PEER_ONLINE}/transfers")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Transferring" in body
    assert "Via relay (metered)" in body
    assert "1 of 4 files" in body
    assert "b.jpg" in body
    assert 'hx-trigger="every 2s"' in body
    assert "cancel" in body


def test_transfers_fragment_idles_without_batches(app, client, service):
    _seed_devices(app)
    resp = client.get(f"/sharing/devices/{PEER_ONLINE}/transfers")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "sharingTransfersChanged from:body" in body
    assert "every 2s" not in body


def test_transfers_fragment_offers_delete_for_inactive_batches(app, client, service):
    _seed_devices(app)
    service.transfer_rows = [
        {
            "id": "batch-1",
            "peer_device_id": PEER_ONLINE,
            "peer_name": "laptop",
            "label": "trip",
            "state": "cancelled",
            "path": "relay",
            "active": False,
            "paused_for_budget": False,
            "files_total": 4,
            "files_done": 1,
            "files_failed": 0,
            "bytes_total": 400,
            "bytes_done": 100,
            "relay_bytes": 100,
            "relay_budget_bytes": 2**30,
            "active_files": [],
            "failed_files": [],
            "error": None,
            "created_at": None,
            "finished_at": None,
        }
    ]

    body = client.get(f"/sharing/devices/{PEER_ONLINE}/transfers").get_data(as_text=True)

    assert "Cancelled" in body
    assert f"/transfers/batch-1/delete" in body
    assert "Delete transfer?" in body


def test_transfers_paused_batch_offers_continue_anyway(app, client, service):
    _seed_devices(app)
    service.transfer_rows = [
        {
            "id": "batch-1",
            "peer_device_id": PEER_ONLINE,
            "peer_name": "laptop",
            "label": "trip",
            "state": "paused_relay_budget",
            "path": "relay",
            "active": True,
            "paused_for_budget": True,
            "files_total": 4,
            "files_done": 1,
            "files_failed": 0,
            "bytes_total": 400,
            "bytes_done": 100,
            "relay_bytes": 2**30,
            "relay_budget_bytes": 2**30,
            "active_files": [],
            "failed_files": [],
            "error": None,
            "created_at": None,
            "finished_at": None,
        }
    ]

    resp = client.get(f"/sharing/devices/{PEER_ONLINE}/transfers")

    body = resp.get_data(as_text=True)
    assert "Continue anyway" in body
    assert f"/transfers/batch-1/continue" in body


def test_cancel_and_continue_transfer_routes(app, client, service):
    _seed_devices(app)

    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/transfers/batch-1/cancel")
    assert resp.status_code == 200
    message, _type = _notification(resp)
    assert "cancelled" in message.lower()
    assert service.cancelled_batches == ["batch-1"]

    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/transfers/batch-1/continue")
    assert resp.status_code == 200
    message, _type = _notification(resp)
    assert "relay" in message.lower()
    assert service.continued_batches == ["batch-1"]


def test_delete_transfer_route(app, client, service):
    _seed_devices(app)

    resp = client.post(f"/sharing/devices/{PEER_ONLINE}/transfers/batch-1/delete")

    assert resp.status_code == 200
    message, type_ = _notification(resp)
    assert message == "Transfer deleted." and type_ == "success"
    assert service.deleted_batches == ["batch-1"]


def test_pull_remote_file_requires_download_directory(app, client, service):
    _seed_devices(app)

    resp = client.post(
        f"/sharing/devices/{PEER_ONLINE}/pull",
        data={"remote_media_dir_id": "remote-lib", "relative_path": "trip/a.jpg"},
    )

    assert resp.status_code == 204
    message, type_ = _notification(resp)
    assert "Choose a download directory" in message and type_ == "error"


def test_actions_unavailable_without_service(client):
    for path in (
        "/sharing/pairing-code",
        "/sharing/pair",
        "/sharing/revoke",
        f"/sharing/devices/{PEER_ONLINE}/pull",
    ):
        resp = client.post(path, data={"code": "x", "device_id": "y"})
        assert resp.status_code == 204
        message, type_ = _notification(resp)
        assert "not running" in message and type_ == "error"

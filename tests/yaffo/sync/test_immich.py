"""Immich: an API-key, self-hosted service. Sign-in with a key at a server address,
the source (an album or the whole library) and the target (an album per target),
end to end against a fake Immich on a local port."""
from io import BytesIO

import pytest
import requests

from yaffo.db.models import SyncAdapter, SYNC_ITEM_IMPORTED, SYNC_ITEM_LINKED_EXISTING, SYNC_ITEM_SENT
from yaffo.db.repositories import sync_repository
from yaffo.sync import access, connections, credentials, setup
from yaffo.sync.adapter_static import analyze_adapter
from yaffo.sync.engine import run_sync
from yaffo.sync.oauth import SignInError
from yaffo.sync.preset_seed import ensure_presets
from yaffo.sync.preview import RECORD_NEW, preview_sync
from yaffo.sync.providers import IMMICH, InstanceUrlError, bind_instance, normalize_instance_url
from yaffo.sync.schemas import CONNECTION_NEEDS_SIGN_IN, GRANT_ENDPOINT, Selection, WebGrant
from yaffo.sync.web import CONNECTION_TOKEN, WebClient
from yaffo.utils import job_codes as codes

from .conftest import MEDIA_DIR_ID

pytestmark = pytest.mark.unit


@pytest.fixture
def immich():
    from scripts.fake_immich import FakeImmich

    with FakeImmich(photos=5, album="Family") as fake:
        yield fake


@pytest.fixture
def signed_in(session, immich):
    """The Immich account, signed in with its key at the fake's (http, loopback) address."""
    base_url = normalize_instance_url(immich.base_url, allow_http=True)
    return credentials.sign_in_with_secret(session, IMMICH, {"api_key": "immich-test-key"},
                                           {"base_url": base_url}, http=requests.Session())


def _adapter(session, slug):
    ensure_presets(session)
    return session.query(SyncAdapter).filter_by(slug=slug).one()


@pytest.fixture
def immich_source(session, library, signed_in):
    def make(album_id: str = "album-1", label: str = "Family"):
        automation = setup.create_sync(session, "From Immich", _adapter(session, "preset.immich_source").id)
        setup.set_connection(session, automation, signed_in)
        setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Immich")
        setup.set_options(session, automation, {"album": {"id": album_id, "label": label}})
        setup.turn_on(session, automation)
        return automation
    return make


# ---- the server address ----------------------------------------------------------------


@pytest.mark.parametrize(("entered", "stored"), [
    ("photos.example.com", "https://photos.example.com"),
    ("https://Photos.Example.com:2283/", "https://photos.example.com:2283"),
])
def test_a_server_address_is_stored_as_its_origin(entered, stored):
    assert normalize_instance_url(entered) == stored


@pytest.mark.parametrize(("entered", "allow_http", "code"), [
    ("http://photos.example.com", True, "instance_http_public"),
    ("http://192.168.1.20:2283", False, "instance_http_unconfirmed"),
    ("https://photos.example.com/immich", False, "instance_has_path"),
    ("ftp://photos.example.com", False, "instance_invalid"),
    ("https://user:pw@photos.example.com", False, "instance_invalid"),
])
def test_unusable_server_addresses_are_refused(entered, allow_http, code):
    with pytest.raises(InstanceUrlError) as refused:
        normalize_instance_url(entered, allow_http=allow_http)
    assert refused.value.code == code


@pytest.mark.parametrize("host", ["192.168.1.20:2283", "10.0.0.5", "immich.lan", "nas.local", "127.0.0.1:2283"])
def test_http_is_allowed_on_a_home_network_once_confirmed(host):
    assert normalize_instance_url(f"http://{host}", allow_http=True) == f"http://{host}"


# ---- signing in ------------------------------------------------------------------------------


def test_signing_in_checks_the_key_and_names_the_account_per_server(session, immich, signed_in):
    connection = connections.get(session, signed_in)
    host = immich.base_url.split("://", 1)[1]
    assert connection.provider == "immich" and connection.account_id == f"user-1@{host}"
    assert connection.account_label == f"jason@example.com · {host}"
    assert connection.instance == {"base_url": immich.base_url}
    assert immich.state.counters["calls"] == 1   # the check call


def test_a_wrong_key_is_refused_and_nothing_is_stored(session, immich):
    with pytest.raises(SignInError) as refused:
        credentials.sign_in_with_secret(session, IMMICH, {"api_key": "wrong"}, {"base_url": immich.base_url},
                                        http=requests.Session())
    assert refused.value.code == "rejected"
    assert connections.find_for_provider(session, "immich") == []


def test_an_unreachable_server_is_reported(session):
    with pytest.raises(SignInError) as refused:
        credentials.sign_in_with_secret(session, IMMICH, {"api_key": "k"}, {"base_url": "http://127.0.0.1:9"},
                                        http=requests.Session())
    assert refused.value.code == "unreachable"


# ---- the source -------------------------------------------------------------------------------


def test_the_presets_pass_their_static_check_with_search_as_a_read(session):
    for slug in ("preset.immich_source", "preset.immich_target"):
        row = _adapter(session, slug)
        analysis = analyze_adapter(row.published_code)
        assert analysis.ok, analysis.problems
        search = [m for m in analysis.manifest if m["url_pattern"] == "{base_url}/api/search/metadata"]
        assert search and all(m["risk"] == "read" for m in search)


def test_an_album_imports_once_and_later_runs_only_take_new_photos(session, library, immich, immich_source,
                                                                   run_context):
    sync = immich_source()

    first = run_sync(session, sync.id, run_context)
    assert first.outcome == codes.OUTCOME_SYNC_IMPORTED and first.params["imported"] == 5
    assert len(list((library / "Imports" / "Immich").rglob("*.jpg"))) == 5
    assert immich.state.counters["downloads"] == 5

    immich.state.add_photos(2, "Family")
    immich.state.add_photos(1)                      # not in the album
    second = run_sync(session, sync.id, run_context)
    assert second.params["imported"] == 2 and second.params["unchanged"] == 5
    assert immich.state.counters["downloads"] == 7


def test_the_whole_library_can_be_imported(session, immich, immich_source, run_context):
    immich.state.add_photos(3)
    sync = immich_source("library", "Everything in the library")

    assert run_sync(session, sync.id, run_context).params["imported"] == 8


def test_a_photo_the_library_has_is_linked_without_downloading(session, library, immich, immich_source,
                                                               run_context):
    from yaffo.db.models import MediaItem, MEDIA_STATUS_INDEXED
    from yaffo.sync.files import file_sha1

    known = library / "known.jpg"
    known.write_bytes(immich.state.assets["asset-00001"].content)
    session.add(MediaItem(full_file_path=str(known), status=MEDIA_STATUS_INDEXED, sha1=file_sha1(known)))
    session.commit()
    sync = immich_source()

    result = run_sync(session, sync.id, run_context)

    assert result.params["linked"] == 1 and result.params["imported"] == 4
    assert immich.state.counters["downloads"] == 4
    statuses = {row.item_key: row.status for row in sync_repository.rows(session, sync.id)}
    assert statuses["asset-00001"] == SYNC_ITEM_LINKED_EXISTING and statuses["asset-00002"] == SYNC_ITEM_IMPORTED


def test_test_lists_the_album_with_read_only_access(session, immich, immich_source):
    sync = immich_source()

    result = preview_sync(session, sync)

    assert result.problem is None and result.state_counts == {RECORD_NEW: 5}


def test_the_album_picker_offers_the_library_and_the_albums(session, immich, immich_source, run_context):
    sync = immich_source()
    adapter = setup.adapter_of(session, sync)
    access.attach(session, adapter, sync_repository.load_sync(sync).config, read_only=True)

    items = adapter.handler.choices("album", None, None, _ctx(sync, session))["items"]

    assert [(i["id"], i["label"]) for i in items] == [("library", "Everything in the library"), ("album-1", "Family")]


def test_a_revoked_key_pauses_every_sync_until_signed_in_again(session, immich, immich_source, run_context,
                                                               signed_in):
    sync = immich_source()
    immich.state.keys.clear()

    run_sync(session, sync.id, run_context)

    assert connections.get(session, signed_in).status == CONNECTION_NEEDS_SIGN_IN


# ---- the target -------------------------------------------------------------------------------


@pytest.fixture
def immich_target(session, signed_in):
    def make(album_row):
        automation = setup.create_sync(session, "To Immich", _adapter(session, "preset.immich_target").id)
        setup.set_connection(session, automation, signed_in)
        setup.set_selection(session, automation, Selection(kind="album", album_id=album_row.id))
        setup.turn_on(session, automation)
        return automation
    return make


def test_an_album_is_kept_in_an_immich_album_of_the_same_name(session, immich, add_photo, album, immich_target,
                                                              run_context):
    photos = [add_photo(f"mom{i}.jpg", color=(10 * i, 40, 90)) for i in range(3)]
    for_mom = album("For Mom", photos)
    sync = immich_target(for_mom)

    first = run_sync(session, sync.id, run_context)

    assert first.params["sent"] == 3
    created = next(a for a in immich.state.albums.values() if a.name == "For Mom")
    assert len(created.asset_ids) == 3 and immich.state.counters["uploads"] == 3
    rows = sync_repository.rows(session, sync.id)
    assert {r.status for r in rows} == {SYNC_ITEM_SENT} and all(r.details["uploaded"] for r in rows)
    assert run_sync(session, sync.id, run_context).params["sent"] == 0
    assert immich.state.counters["uploads"] == 3


def test_a_photo_leaving_the_album_is_taken_out_and_only_yaffos_upload_trashed(
        session, immich, add_photo, album, immich_target, run_context):
    from yaffo.db.models import AlbumItem

    mine = add_photo("mine.jpg", color=(1, 2, 3))
    theirs = add_photo("theirs.jpg", color=(4, 5, 6))
    # Immich already has "theirs" (its phone app uploaded it): the upload is a duplicate.
    immich.state.add_photos(0)
    from scripts.fake_immich import Asset
    immich.state.assets["asset-phone"] = Asset(id="asset-phone", name="theirs.jpg",
                                               content=open(theirs.full_file_path, "rb").read(),
                                               created="2024-07-04T18:22:10.000Z")
    for_mom = album("For Mom", [mine, theirs])
    sync = immich_target(for_mom)
    run_sync(session, sync.id, run_context)
    created = next(a for a in immich.state.albums.values() if a.name == "For Mom")
    assert "asset-phone" in created.asset_ids

    session.query(AlbumItem).filter(AlbumItem.album_id == for_mom.id).delete()
    session.commit()
    result = run_sync(session, sync.id, run_context)

    assert result.params["removed"] == 2 and created.asset_ids == []
    uploaded = next(a for a in immich.state.assets.values() if a.uploaded)
    assert uploaded.trashed and not immich.state.assets["asset-phone"].trashed


def test_a_photo_already_in_the_album_stays_when_it_leaves_the_selection(
        session, immich, add_photo, album, immich_target, run_context):
    """Sending into the user's own album: what was there before isn't Yaffo's to take out."""
    from yaffo.db.models import AlbumItem
    from scripts.fake_immich import Asset

    theirs = add_photo("theirs.jpg", color=(7, 8, 9))
    immich.state.assets["asset-phone"] = Asset(id="asset-phone", name="theirs.jpg",
                                               content=open(theirs.full_file_path, "rb").read(),
                                               created="2024-07-04T18:22:10.000Z")
    immich.state.albums["album-1"].asset_ids.append("asset-phone")      # already in Family
    mine = add_photo("mine.jpg", color=(1, 2, 3))
    family = album("Family", [theirs, mine])
    sync = immich_target(family)
    setup.set_options(session, sync, {"album": {"id": "album-1", "label": "Family"}})
    setup.turn_on(session, sync)
    run_sync(session, sync.id, run_context)
    assert "asset-phone" in immich.state.albums["album-1"].asset_ids

    session.query(AlbumItem).filter(AlbumItem.album_id == family.id).delete()
    session.commit()
    run_sync(session, sync.id, run_context)

    members = immich.state.albums["album-1"].asset_ids
    assert "asset-phone" in members and not immich.state.assets["asset-phone"].trashed
    assert not any(immich.state.assets[i].uploaded for i in members)   # Yaffo's own upload went


def test_two_way_sync_moves_no_duplicates(session, library, immich, immich_source, add_photo, album,
                                          immich_target, run_context):
    source = immich_source()
    run_sync(session, source.id, run_context)
    from yaffo.db.models import MediaItem
    from yaffo.sync.files import file_sha1
    imported = session.query(MediaItem).all()
    for item in imported:
        item.sha1 = file_sha1(__import__("pathlib").Path(item.full_file_path))
    session.commit()
    everything = album("Everything", imported)
    target = immich_target(everything)

    sent = run_sync(session, target.id, run_context)

    assert sent.params["sent"] == 5
    assert len([a for a in immich.state.assets.values() if not a.trashed]) == 5   # no copies in Immich
    assert run_sync(session, source.id, run_context).params["imported"] == 0     # none back into Yaffo


# ---- web access ------------------------------------------------------------------------------


class RecordingHTTP:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, dict(kwargs.get("headers") or {})))
        response = requests.Response()
        response.status_code = 200
        response.raw = BytesIO(b"{}")
        response.url = url
        return response


class FixedKey(credentials.ApiKeyCredentials):
    def __init__(self):
        super().__init__(IMMICH, {"api_key": "secret-key"})


def _client(base_url, pattern="{base_url}/api/albums", method="GET"):
    grant = WebGrant(method=method, url_pattern=pattern, grant_kind=GRANT_ENDPOINT, followup_of=None, risk="read",
                     granted_code_hash="h", granted_at="2026-10-10T00:00:00Z")
    http = RecordingHTTP()
    client = WebClient(provider=bind_instance(IMMICH, {"base_url": base_url}), grants=[grant], code_hash="h",
                       tokens=None, credentials=FixedKey(), base_url=base_url, http=http)
    return client, http


def test_the_key_goes_only_to_the_signed_in_server():
    client, http = _client("http://192.168.1.20:2283")

    assert client.web_request(CONNECTION_TOKEN, "GET", "{base_url}/api/albums")["status"] == 200
    assert http.calls[0][1] == "http://192.168.1.20:2283/api/albums"
    assert http.calls[0][2]["x-api-key"] == "secret-key"
    denied = client.web_request(CONNECTION_TOKEN, "GET", "https://evil.example/api/albums")
    assert denied["status"] == 0 and len(http.calls) == 1


def test_http_is_refused_except_to_the_signed_in_server():
    client, http = _client("https://immich.example.com")

    assert client.web_request(CONNECTION_TOKEN, "GET", "http://immich.example.com/api/albums")["status"] == 0
    assert client.web_request(CONNECTION_TOKEN, "GET", "{base_url}/api/albums")["status"] == 200
    assert http.calls[0][1] == "https://immich.example.com/api/albums"


def _ctx(automation, session):
    from yaffo.sync.handlers import HookContext
    from yaffo.sync.schemas import PASS_INCREMENTAL
    column = sync_repository.load_sync(automation)
    return HookContext(options=column.config.options, state=column.runtime.state, run_id=None,
                       pass_kind=PASS_INCREMENTAL, preview=True, automation_id=automation.id)


@pytest.mark.parametrize("allow_http", [False, True])
def test_an_address_without_a_scheme_is_https(allow_http):
    assert normalize_instance_url("127.0.0.1:2283", allow_http=allow_http) == "https://127.0.0.1:2283"


def test_https_to_an_http_only_server_says_so(session):
    """An http server (Node, Flask) answers a TLS handshake with a plain HTTP 400."""
    import socket
    import threading

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def answer_in_plain_http():
        connection, _ = listener.accept()
        connection.recv(4096)
        connection.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        connection.close()

    threading.Thread(target=answer_in_plain_http, daemon=True).start()
    https_url = f"https://127.0.0.1:{listener.getsockname()[1]}"

    with pytest.raises(SignInError) as refused:
        credentials.sign_in_with_secret(session, IMMICH, {"api_key": "immich-test-key"}, {"base_url": https_url},
                                        http=requests.Session())
    listener.close()
    assert refused.value.code == "tls_failed"

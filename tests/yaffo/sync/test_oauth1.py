"""OAuth 1.0a: request signing (checked against oauthlib's result for Twitter's published
example), the connection block, and a custom Flickr target end to end against the
fake Flickr, which verifies the signature of every request it gets."""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from yaffo.db.models import SYNC_ADAPTER_STATUS_READY, AlbumItem, SyncAdapter
from yaffo.db.repositories import sync_repository
from yaffo.sync import connections, oauth1, secrets_store, setup
from yaffo.sync.adapter_static import CURRENT_CONTRACT, analyze_adapter
from yaffo.sync.adapters import load_adapter
from yaffo.sync.engine import run_sync
from yaffo.sync.oauth import SignInError
from yaffo.sync.preset_seed import grants_from_manifest
from yaffo.sync.providers import AUTH_OAUTH1, ConnectionBlockError, provider_from_block
from yaffo.sync.schemas import CONNECTION_NEEDS_SIGN_IN, AdapterVersion, Selection

pytestmark = pytest.mark.unit

EXAMPLE = Path(__file__).parents[3] / "yaffo" / "site_agents" / "adapter" / "examples" / "flickr_target.star"


# ---- signing ------------------------------------------------------------------------------------


def test_a_request_is_signed_as_oauthlib_signs_it():
    """Twitter's documented example: oauthlib gives hCtSmYh+iHYCEqBWrE7C7hYmtUk= too."""
    header = oauth1.sign(
        "POST", "https://api.twitter.com/1.1/statuses/update.json?include_entities=true",
        "xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
        "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
        body=[("status", "Hello Ladies + Gentlemen, a signed OAuth request!")],
        nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp="1318622958")

    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in header  # gitleaks:allow (Twitter's published example)
    assert header.startswith('OAuth oauth_consumer_key="xvz1evFS4wEEPTGEFPHBog"')  # gitleaks:allow (Twitter's published example)


def test_the_signature_base_normalizes_the_url_and_parameters():
    base = oauth1.signature_base("get", "HTTPS://API.Example.com:443/a b?z=2&a=1&a=0", [("oauth_x", "é ~")])

    assert base == ("GET&https%3A%2F%2Fapi.example.com%2Fa%2520b&"
                    "a%3D0%26a%3D1%26oauth_x%3D%25C3%25A9%2520~%26z%3D2")


# ---- the connection block ----------------------------------------------------------------------


def test_an_oauth1a_block_becomes_a_provider():
    analysis = analyze_adapter(EXAMPLE.read_text())
    assert analysis.ok, analysis.problems

    provider = provider_from_block("custom:flickr", "Flickr", analysis.metadata["connection"],
                                   app_credentials=lambda: {"client_id": "k", "client_secret": "s"})

    assert provider.auth_kind == AUTH_OAUTH1 and provider.is_oauth and not provider.is_oauth2
    assert provider.request_token_url == "https://www.flickr.com/services/oauth/request_token"
    assert provider.extra_authorize_params == (("perms", "delete"),) and provider.sign_multipart_fields
    assert provider.client_id() == "k" and provider.account_id_field == "user.id"


@pytest.mark.parametrize(("change", "message"), [
    ({"redirect": "any_loopback"}, "oauth1a redirect must be one of"),
    ({"auth": {"authorize_url": "https://x.example/a"}}, "auth.request_token_url must be an https URL"),
    ({"auth": {"request_token_url": "https://x.example/r", "authorize_url": "https://x.example/a",
               "access_token_url": "https://x.example/t", "authorize_params": {"perms": 1}}}, "authorize_params"),
])
def test_oauth1a_blocks_that_break_a_rule_are_refused(change, message):
    block = {**analyze_adapter(EXAMPLE.read_text()).metadata["connection"], **change}
    with pytest.raises(ConnectionBlockError) as refused:
        provider_from_block("custom:x", "X", block)
    assert message in str(refused.value)


# ---- Flickr end to end -------------------------------------------------------------------------


@pytest.fixture
def flickr(monkeypatch):
    from scripts.fake_flickr import FakeFlickr

    with FakeFlickr() as fake:
        monkeypatch.setenv("YAFFO_SYNC_ORIGIN_OVERRIDES", fake.overrides)
        yield fake


@pytest.fixture
def flickr_adapter(session):
    from scripts.fake_flickr import CONSUMER_KEY, CONSUMER_SECRET

    code = EXAMPLE.read_text()
    analysis = analyze_adapter(code)
    row = SyncAdapter(slug="custom.flickr-sets", name="Flickr sets", role="target", provider="custom:custom.flickr-sets",
                      is_preset=False, status=SYNC_ADAPTER_STATUS_READY, doc_domains=[], working_code=code,
                      published_code=code, app_secret_ref="sync.app.custom.flickr-sets",
                      published=AdapterVersion(code_hash=analysis.code_hash, contract=CURRENT_CONTRACT,
                                               metadata=analysis.metadata, manifest=analysis.manifest).to_dict(),
                      grants=grants_from_manifest(analysis.manifest, analysis.code_hash))
    row.working = row.published
    session.add(row)
    session.commit()
    secrets_store.store_secret(row.app_secret_ref, {"client_id": CONSUMER_KEY, "client_secret": CONSUMER_SECRET})
    return row


def sign_in(session, adapter, automation_slug=None) -> str:
    """Start the sign-in, approve it at the fake, and finish it from the return."""
    authorize = oauth1.begin_sign_in(session, adapter.preset, 5001, "http://localhost:5001/back", automation_slug,
                                     http=requests.Session())
    assert parse_qs(urlsplit(authorize).query)["perms"] == ["delete"]
    approve = requests.get(authorize.replace("/services/oauth/authorize?", "/_approve?"), allow_redirects=False,
                           timeout=10)
    returned = urlsplit(approve.headers["Location"])
    assert returned.netloc == "127.0.0.1:5001" and returned.path in ("", "/")
    params = {k: v[0] for k, v in parse_qs(returned.query).items()}
    return oauth1.finish_sign_in(session, params, http=requests.Session()).connection_key


def test_signing_in_trades_the_request_token_and_names_the_account(session, flickr, flickr_adapter):
    adapter = load_adapter(session, flickr_adapter.id)

    key = sign_in(session, adapter)

    connection = connections.get(session, key)
    assert connection.provider == "custom:custom.flickr-sets" and connection.account_id == "12345678@N01"
    assert connection.account_label == "jturan"
    stored = secrets_store.load_secret(connection.secret_ref)
    assert stored["token"].startswith("AT-") and stored["token_secret"]


def test_a_sign_in_without_the_app_credentials_is_refused(session, flickr, flickr_adapter):
    secrets_store.delete_secret(flickr_adapter.app_secret_ref)

    with pytest.raises(SignInError) as refused:
        oauth1.begin_sign_in(session, load_adapter(session, flickr_adapter.id).preset, 5001, "/", http=requests.Session())
    assert refused.value.code == "app_missing"


def test_a_return_can_be_used_once(session, flickr, flickr_adapter):
    adapter = load_adapter(session, flickr_adapter.id)
    authorize = oauth1.begin_sign_in(session, adapter.preset, 5001, "/", http=requests.Session())
    approve = requests.get(authorize.replace("/services/oauth/authorize?", "/_approve?"), allow_redirects=False,
                           timeout=10)
    params = {k: v[0] for k, v in parse_qs(urlsplit(approve.headers["Location"]).query).items()}
    oauth1.finish_sign_in(session, params, http=requests.Session())

    with pytest.raises(SignInError):
        oauth1.finish_sign_in(session, params, http=requests.Session())


@pytest.fixture
def flickr_sync(session, flickr, flickr_adapter, add_photo, album):
    photos = [add_photo(f"mom{i}.jpg", color=(20 * i, 60, 90)) for i in range(3)]
    for_mom = album("For Mom", photos)
    adapter = load_adapter(session, flickr_adapter.id)
    automation = setup.create_sync(session, "To Flickr", flickr_adapter.id)
    setup.set_connection(session, automation, sign_in(session, adapter))
    setup.set_selection(session, automation, Selection(kind="album", album_id=for_mom.id))
    setup.turn_on(session, automation)
    return automation, for_mom


def test_an_album_goes_to_a_flickr_set_through_signed_requests(session, flickr, flickr_sync, run_context):
    automation, _ = flickr_sync

    first = run_sync(session, automation.id, run_context)

    assert first.params["sent"] == 3 and first.problem is None
    assert flickr.state.counters["uploads"] == 3 and flickr.state.counters["sets_created"] == 1
    (members,) = [s["photos"] for s in flickr.state.sets.values() if s["title"] == "For Mom"]
    assert len(members) == 3
    assert {r.details["photoset_id"] for r in sync_repository.rows(session, automation.id)} == set(flickr.state.sets)
    assert run_sync(session, automation.id, run_context).params["sent"] == 0
    assert flickr.state.counters["uploads"] == 3


def test_photos_leaving_the_album_are_taken_out_and_deleted(session, flickr, flickr_sync, run_context):
    automation, for_mom = flickr_sync
    run_sync(session, automation.id, run_context)
    session.query(AlbumItem).filter(AlbumItem.album_id == for_mom.id).delete()
    session.commit()

    result = run_sync(session, automation.id, run_context)

    assert result.params["removed"] == 3
    assert flickr.state.photos == {} and flickr.state.counters["deleted"] == 3


def test_a_revoked_token_stops_the_sync_until_signed_in_again(session, flickr, flickr_sync, run_context):
    automation, _ = flickr_sync
    flickr.state.access_tokens.clear()

    run_sync(session, automation.id, run_context)

    key = sync_repository.load_sync(automation).config.connection_key
    assert connections.get(session, key).status == CONNECTION_NEEDS_SIGN_IN

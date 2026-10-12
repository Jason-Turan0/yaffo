"""A custom adapter's own connection block: its rules, how the static check uses it,
and a custom adapter running end to end through it (the Immich source, declared as a
custom adapter, against the fake Immich)."""
from pathlib import Path

import pytest
import requests

from yaffo.db.models import SYNC_ADAPTER_STATUS_READY, SyncAdapter
from yaffo.sync import credentials, setup
from yaffo.sync.adapter_static import CURRENT_CONTRACT, analyze_adapter
from yaffo.sync.adapters import load_adapter
from yaffo.sync.engine import run_sync
from yaffo.sync.preset_seed import grants_from_manifest
from yaffo.sync.providers import (
    AUTH_API_KEY, AUTH_BASIC, AUTH_OAUTH2, ConnectionBlockError, normalize_instance_url, provider_from_block,
)
from yaffo.sync.schemas import AdapterVersion

from .conftest import MEDIA_DIR_ID

pytestmark = pytest.mark.unit

PRESETS = Path(__file__).parents[3] / "yaffo" / "sync" / "presets"

PIXELFED = {
    "auth_kind": "api_key",
    "base_urls": ["https://pixelfed.social"],
    "check": {"method": "GET", "url": "https://pixelfed.social/api/v1/accounts/verify_credentials",
              "account_id": "id", "account_label": ["username"]},
    "api_key": {"in": "header", "name": "Authorization", "prefix": "Bearer "},
    "key_label": "Personal access token",
}


def test_an_api_key_block_becomes_a_provider():
    provider = provider_from_block("custom:pixelfed", "Pixelfed", PIXELFED)

    assert provider.key == "custom:pixelfed" and provider.auth_kind == AUTH_API_KEY
    assert provider.base_urls == ("https://pixelfed.social",) and provider.api_key.prefix == "Bearer "
    assert provider.account_label_fields == ("username",) and provider.key_label == "Personal access token"


def test_a_self_hosted_basic_block():
    provider = provider_from_block("custom:dav", "Nextcloud", {
        "auth_kind": "basic", "instance": ["base_url"],
        "check": {"url": "{base_url}/ocs/v2.php/cloud/user?format=json"}})

    assert provider.auth_kind == AUTH_BASIC and provider.instance and provider.base_urls == ("{base_url}",)


def test_an_oauth2_block_takes_its_app_from_the_keychain():
    provider = provider_from_block("custom:dropbox", "Dropbox", {
        "auth_kind": "oauth2", "base_urls": ["https://api.dropboxapi.com"], "redirect": "any_loopback",
        "auth": {"authorize_url": "https://www.dropbox.com/oauth2/authorize",
                 "token_url": "https://api.dropboxapi.com/oauth2/token", "scopes": ["files.content.read"]},
        "check": {"url": "https://api.dropboxapi.com/2/users/get_current_account"}},
        app_credentials=lambda: {"client_id": "app-1", "client_secret": "s3cret"})

    assert provider.auth_kind == AUTH_OAUTH2 and provider.client_id() == "app-1"
    assert provider.client_secret() == "s3cret" and provider.base_scopes == ("files.content.read",)


@pytest.mark.parametrize(("change", "message"), [
    ({"auth_kind": "sigv4"}, "isn't supported yet"),
    ({"auth_kind": "cookies"}, "auth_kind must be one of"),
    ({"base_urls": ["http://pixelfed.social"]}, "plain https origin"),
    ({"base_urls": ["https://pixelfed.social/api"]}, "plain https origin"),
    ({"base_urls": []}, "base_urls must list"),
    ({"check": {"url": "https://elsewhere.example/me"}}, "check url must be on base_urls"),
    ({"check": {"method": "POST", "url": "https://pixelfed.social/x"}}, "read-only call"),
    ({"api_key": {"in": "header", "name": "Cookie"}}, "can't go in the Cookie header"),
    ({"api_key": {"in": "body"}}, "where the key goes"),
    ({"instance": ["base_url"]}, "no fixed base_urls"),
    ({"script_login": True}, "unknown keys"),
])
def test_blocks_that_break_a_rule_are_refused(change, message):
    with pytest.raises(ConnectionBlockError) as refused:
        provider_from_block("custom:x", "X", {**PIXELFED, **change})
    assert message in str(refused.value)


def _custom_immich_code() -> str:
    """The Immich source preset, re-declared as a custom adapter with its own block."""
    code = (PRESETS / "immich_source.star").read_text()
    block = ('"provider": "custom",\n    "connection": {"auth_kind": "api_key", "instance": ["base_url"], '
             '"check": {"method": "GET", "url": "{base_url}/api/users/me", "account_label": ["email"]}, '
             '"api_key": {"in": "header", "name": "x-api-key"}},\n')
    return code.replace('"provider": "immich",\n', block)


def test_the_static_check_holds_a_custom_adapter_to_its_own_hosts():
    assert analyze_adapter(_custom_immich_code()).ok

    off_host = _custom_immich_code().replace('"{base_url}/api/albums"', '"https://evil.example/api/albums"')
    codes = {p.code for p in analyze_adapter(off_host).problems}
    assert "url_outside_provider" in codes

    no_block = (PRESETS / "immich_source.star").read_text().replace('"provider": "immich"', '"provider": "custom"')
    assert "connection_missing" in {p.code for p in analyze_adapter(no_block).problems}

    on_preset = _custom_immich_code().replace('"provider": "custom"', '"provider": "immich"')
    assert "connection_on_preset" in {p.code for p in analyze_adapter(on_preset).problems}


def test_a_custom_adapter_signs_in_and_runs_through_its_block(session, library, run_context):
    from scripts.fake_immich import FakeImmich

    code = _custom_immich_code()
    analysis = analyze_adapter(code)
    row = SyncAdapter(slug="custom.my_immich", name="My Immich", role="source", provider="custom:custom.my_immich",
                      is_preset=False, status=SYNC_ADAPTER_STATUS_READY, doc_domains=[],
                      working_code=code, published_code=code,
                      published=AdapterVersion(code_hash=analysis.code_hash, contract=CURRENT_CONTRACT,
                                               metadata=analysis.metadata, manifest=analysis.manifest).to_dict(),
                      grants=grants_from_manifest(analysis.manifest, analysis.code_hash))
    session.add(row)
    session.commit()
    adapter = load_adapter(session, row.id)
    assert adapter.provider == "custom:custom.my_immich" and adapter.preset.instance

    with FakeImmich(photos=3, album="Family") as immich:
        key = credentials.sign_in_with_secret(session, adapter.preset, {"api_key": "immich-test-key"},
                                              {"base_url": normalize_instance_url(immich.base_url, allow_http=True)},
                                              http=requests.Session())
        automation = setup.create_sync(session, "Custom Immich", row.id)
        setup.set_connection(session, automation, key)
        setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Custom")
        setup.set_options(session, automation, {"album": {"id": "album-1", "label": "Family"}})
        setup.turn_on(session, automation)

        result = run_sync(session, automation.id, run_context)

    assert result.params["imported"] == 3

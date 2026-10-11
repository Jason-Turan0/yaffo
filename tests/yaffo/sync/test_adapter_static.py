"""The adapter static check: metadata extraction, contract rules, endpoint detection."""
from pathlib import Path

import pytest

from yaffo.sync.adapter_static import CURRENT_CONTRACT, analyze_adapter, pattern_regex
from yaffo.sync.schemas import GRANT_ENDPOINT, GRANT_FOLLOWUP

PRESET = Path(__file__).resolve().parents[3] / "yaffo" / "sync" / "presets" / "onedrive_source.star"

GRAPH = "https://graph.microsoft.com/v1.0"


def adapter(body: str, metadata: str | None = None) -> str:
    metadata = metadata or (
        '{"contract": 1, "role": "source", "provider": "onedrive", "title": "T", '
        '"hooks": {"list_page": list_page}}')
    return f'GRAPH = "{GRAPH}"\n\n{body}\n\nADAPTER = {metadata}\n'


def codes(analysis) -> list[str]:
    return [p.code for p in analysis.problems]


def test_the_onedrive_preset_passes_and_its_endpoints_are_detected():
    analysis = analyze_adapter(PRESET.read_text())
    assert analysis.ok, analysis.problems
    assert analysis.metadata["hooks"] == {"list_page": "list_page", "choices": "choices"}
    assert analysis.metadata["scopes"] == ["Files.Read"]
    endpoints = {(e["method"], e["url_pattern"].split("?")[0]) for e in analysis.manifest
                 if e["grant_kind"] == GRANT_ENDPOINT}
    assert endpoints == {
        ("GET", f"{GRAPH}/me/drive/items/{{id}}"),             # a folder's path, for keep-the-source's-folders
        ("GET", f"{GRAPH}/me/drive/items/{{id}}/delta"),
        ("GET", f"{GRAPH}/me/drive/items/{{id}}/children"),
        ("GET", f"{GRAPH}/me/drive/root/children"),
        ("GET", f"{GRAPH}/me/drive/special/cameraroll"),
    }
    # nextLink / deltaLink calls can't be resolved: one follow-up grant for GET.
    assert [(e["method"], e["url_pattern"]) for e in analysis.manifest if e["grant_kind"] == GRANT_FOLLOWUP] \
        == [("GET", None)]
    assert all(e["risk"] == "read" for e in analysis.manifest)


def test_literal_constant_and_template_urls_resolve():
    code = adapter('''
def list_page(cursor, ctx):
    a = web_request(CONNECTION, "GET", GRAPH + "/me")
    b = web_request(CONNECTION, "POST", url_template(GRAPH + "/items/{id}/copy", id=cursor))
    return {"records": [], "next_cursor": None}
''')
    analysis = analyze_adapter(code)
    assert analysis.ok, analysis.problems
    assert {(e["method"], e["url_pattern"], e["risk"]) for e in analysis.manifest} == {
        ("GET", f"{GRAPH}/me", "read"), ("POST", f"{GRAPH}/items/{{id}}/copy", "write")}


@pytest.mark.parametrize("body, code", [
    ('def list_page(cursor, ctx):\n    return web_request("other", "GET", GRAPH + "/me")', "connection_not_literal"),
    ('def list_page(cursor, ctx):\n    m = ctx["m"]\n    return web_request(CONNECTION, m, GRAPH + "/me")',
     "method_not_literal"),
    ('def list_page(cursor, ctx):\n    return web_request(CONNECTION, "GET", GRAPH + "/items/" + cursor)',
     "url_concatenation"),
    ('def list_page(cursor, ctx):\n    return web_request(CONNECTION, "GET", "https://evil.example.com/x")',
     "url_outside_provider"),
    ('def list_page(cursor, ctx):\n    return web_request(CONNECTION, "GET", "http://graph.microsoft.com/x")',
     "url_not_https"),
    ('def list_page(cursor, ctx):\n    return url_template(cursor, id=1)', "template_not_literal"),
])
def test_unresolvable_or_foreign_endpoints_are_errors(body, code):
    assert code in codes(analyze_adapter(adapter(body)))


def test_metadata_rules():
    def check(metadata, body="def list_page(cursor, ctx):\n    return {}"):
        return codes(analyze_adapter(adapter(body, metadata)))

    assert "contract_outdated" in check(
        '{"contract": 0, "role": "source", "provider": "onedrive", "title": "T", "hooks": {"list_page": list_page}}')
    assert "missing_hook" in check('{"contract": 1, "role": "source", "provider": "onedrive", "title": "T", "hooks": {}}')
    assert "unknown_hook" in check(
        '{"contract": 1, "role": "source", "provider": "onedrive", "title": "T", '
        '"hooks": {"list_page": list_page, "push": list_page}}')
    assert "hook_not_a_function" in check(
        '{"contract": 1, "role": "source", "provider": "onedrive", "title": "T", "hooks": {"list_page": nope}}')
    assert "choices_missing" in check(
        '{"contract": 1, "role": "source", "provider": "onedrive", "title": "T", '
        '"options": {"f": {"type": "remote_folder", "label": "F"}}, "hooks": {"list_page": list_page}}')
    assert "create_not_collection" in check(
        '{"contract": 1, "role": "source", "provider": "onedrive", "title": "T", '
        '"options": {"f": {"type": "string", "label": "F", "create": True}}, "hooks": {"list_page": list_page}}')
    assert "unknown_provider" in check(
        '{"contract": 1, "role": "source", "provider": "dropbox", "title": "T", "hooks": {"list_page": list_page}}')
    assert "connection_missing" in check(
        '{"contract": 1, "role": "source", "provider": "custom", "title": "T", "hooks": {"list_page": list_page}}')
    assert "metadata_invalid" in check(
        '{"contract": 1, "role": "source", "provider": "onedrive", "title": "T", "hooks": {"list_page": list_page}, '
        '"surprise": 1}')
    assert "adapter_not_literal" in check(
        '{"contract": 1, "role": "source", "provider": "onedrive", "title": GRAPH.upper(), '
        '"hooks": {"list_page": list_page}}')


def test_target_capabilities_must_match_hooks():
    body = "def push(batch, ctx):\n    return {}\n\ndef remove(items, ctx):\n    return {}"
    found = codes(analyze_adapter(adapter(body, (
        '{"contract": 1, "role": "target", "provider": "onedrive", "title": "T", '
        '"capabilities": {"reconcile": True}, "hooks": {"push": push, "remove": remove}}'))))
    assert "capability_without_hook" in found and "hook_without_capability" in found


def test_top_level_host_calls_and_statements_are_rejected():
    code = adapter('X = web_request(CONNECTION, "GET", GRAPH)\nprint("hi")\n'
                   'def list_page(cursor, ctx):\n    return {}')
    found = codes(analyze_adapter(code))
    assert "top_level_host_call" in found and "top_level_statement" in found


def test_adapter_must_be_assigned_once_and_parse():
    assert "adapter_reassigned" in codes(analyze_adapter(
        adapter("def list_page(cursor, ctx):\n    return {}") + "ADAPTER = {}\n"))
    assert codes(analyze_adapter("def (")) == ["syntax"]
    assert "no_adapter" in codes(analyze_adapter("def list_page(cursor, ctx):\n    return {}\n"))


def test_contract_constant():
    assert CURRENT_CONTRACT == 1


def test_pattern_regex_matches_one_segment_or_a_path():
    single = pattern_regex(f"{GRAPH}/me/drive/items/{{id}}/children?x=1")
    assert single.match(f"{GRAPH}/me/drive/items/AB12/children")
    assert not single.match(f"{GRAPH}/me/drive/items/AB/12/children")
    multi = pattern_regex(f"{GRAPH}/me/drive/root:/{{path}}:/content")
    assert multi.match(f"{GRAPH}/me/drive/root:/Pictures/For%20Mom/a.jpg:/content")

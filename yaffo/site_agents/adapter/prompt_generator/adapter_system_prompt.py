"""The service builder's system prompt: the contract a custom sync adapter is written
against, in XML-delimited sections.

Keep this STABLE: it's the cached prefix of every generation. Per-service things (the
draft, the last Test) are in the user turn. The host API, error codes, record fields,
option types and connection kinds are read from the code that enforces them, so the
prompt can't promise what the host doesn't do. The worked examples are the shipped
Immich source preset, re-declared as a custom adapter, and a Flickr target
(../examples/flickr_target.star, OAuth 1.0a), both checked by the tests.
"""
from __future__ import annotations

from pathlib import Path

from yaffo.site_agents.common.prompt_generator.response_language import response_language_block
from yaffo.site_agents.common.prompt_generator.xml_helpers import block
from yaffo.sync.adapter_static import CURRENT_CONTRACT, OPTIONAL_HOOKS, REQUIRED_HOOKS
from yaffo.sync.handlers import ERROR_CODES
from yaffo.sync.options import OPTION_TYPES
from yaffo.sync.providers import CUSTOM_AUTH_KINDS, LATER_AUTH_KINDS, REDIRECT_KINDS
from yaffo.sync.starlark_adapter import ADAPTER_HOST_API

PRESETS = Path(__file__).resolve().parents[3] / "sync" / "presets"
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _role() -> str:
    return block("role", [
        "You write custom sync adapters for Yaffo, a personal photo library app.",
        "An adapter connects Yaffo to one photo service in one direction: a SOURCE lists",
        "the service's photos so Yaffo can import new ones; a TARGET sends Yaffo photos to",
        "the service and keeps them there. Yaffo's host runs everything else: diffing,",
        "the item ledger, retries, downloads, file writes, indexing, crash recovery.",
        "You write only the provider-specific part: one Starlark module, roughly 50 to 150",
        "lines, and its connection block (how the service signs in and which hosts it uses).",
    ])


def _workflow() -> str:
    return block("workflow", [
        "1. Work out the service's API: sign-in method, hosts, list/upload/album endpoints,",
        "   response shapes. Read its documentation with fetch_docs: an OpenAPI spec is",
        "   best; most services keep API docs at well-known addresses. Ask for every page",
        "   you need in one batch. Reading from a new domain needs the user's OK: when a",
        "   batch waits for it, end your turn, and you'll continue with their answer. There",
        "   is no web search; never invent endpoints or fields, say what you're unsure of.",
        "2. Call write_adapter_code with the whole module. It's statically checked; fix every",
        "   reported problem and write again. Each call replaces the draft.",
        "3. Call test_adapter. Writes are never sent. Reads go to the user's test account",
        "   once they've allowed that (the first Test with a test account asks them; until",
        "   then reads are recorded). Without a test account, ask the user to connect one",
        "   on the Test account tab. The user's own Test button always reads live, and its",
        "   result reaches you with their next message. Check field names against real",
        "   responses before you call a draft done.",
        "4. When the Test passes, tell the user they can publish. Publishing, and approving",
        "   the endpoint list, is the user's step, never yours.",
        "Keep replies short: what you did, what the user should do next.",
    ])


def _language() -> str:
    return block("starlark", [
        "Starlark is Python-like but deterministic and sandboxed: for/if and",
        "comprehensions are fine; no while loops, recursion, imports, try/except, classes,",
        "or lambda. No I/O except the host functions below. Each hook call runs in a fresh",
        "evaluator: module-level state doesn't survive between calls; keep what must",
        "persist in ctx['state'] (read) and return it as state_patch.",
        "Module top level may only define constants and functions, and must assign ADAPTER",
        "exactly once, as a dict literal whose 'hooks' values are names of top-level defs.",
    ])


def _host_api() -> str:
    lines = [f"- {f.signature}: {f.description}" for f in ADAPTER_HOST_API]
    return block("host_api", lines + [
        "CONNECTION is a host value: always pass it as the first argument of web_request /",
        "web_upload_media. The host adds the account's credentials; code never sees a",
        "token, key, or password, and must not try to put one in a URL or header.",
    ])


def _urls() -> str:
    return block("urls", [
        "Every call is authorized by a static check of the code, then by the user at publish:",
        "- The method is a literal. The URL is a literal, a module constant, or",
        "  url_template('<literal>', name=value); concatenating a variable onto a literal is",
        "  an error (use url_template).",
        "- URLs are https, on the connection block's base_urls.",
        "- A self-hosted service (connection 'instance': ['base_url']) writes every URL as",
        "  '{base_url}/path...'. The host fills in the server the user signed in to.",
        "- A key placed in the path is '{secret}' in the URL; the host fills it in.",
        "- A URL taken from a response (a next-page link) may be called only because a",
        "  granted call returned it; that's allowed automatically.",
        "- Reads are GET/HEAD. A POST that only reads (a search) can be declared in",
        "  ADAPTER['reads'] as 'POST <url pattern exactly as in the code>' so Test and the",
        "  option pickers may call it.",
    ])


def _connection() -> str:
    return block("connection_block", [
        'ADAPTER["provider"] is "custom" and ADAPTER["connection"] declares the sign-in:',
        f"- auth_kind: one of {list(CUSTOM_AUTH_KINDS)}. {list(LATER_AUTH_KINDS)} aren't",
        "  supported yet: if the service needs one, say so instead of building it.",
        "- base_urls: the service's https origins (no paths), or omit them and set",
        "  instance: ['base_url'] for a self-hosted service (Immich, Nextcloud, ...).",
        "- followup_hosts: hosts that only receive URLs the service returned (download",
        "  links); never sent credentials. A leading *. is allowed.",
        "- check: {'method': 'GET', 'url': <read-only 'who am I' URL on base_urls>,",
        "  'account_id': <field with a stable id>, 'account_label': [<fields for a label>]}.",
        "- api_key: {'in': 'header'|'query'|'path', 'name': <header or parameter>,",
        "  'prefix': 'Bearer '}, with optional key_label and key_help (where to find it).",
        "- basic: nothing more (a username and an app password).",
        "- oauth2: auth: {authorize_url, token_url, scopes, device_code_url?},",
        f"  redirect: one of {list(REDIRECT_KINDS)}, app_credentials: ['client_id',",
        "  'client_secret'] (the user registers their own app on the Test account tab).",
        "- oauth1a: auth: {request_token_url, authorize_url, access_token_url,",
        "  authorize_params? (e.g. {'perms': 'write'}), sign_multipart_fields? (true when the",
        "  service signs an upload's form fields, as Flickr does)}, redirect: 'registered' or",
        "  'per_request'. The user always registers their own app (key and secret). Yaffo",
        "  signs every request; the check's account_id and account_label may be dotted",
        "  paths into the response ('user.username._content').",
        "Changing the block (a host, a scope) is a change the user re-approves.",
    ])


def _contract() -> str:
    roles = [f"- {role}: required {sorted(REQUIRED_HOOKS[role])}, optional {sorted(OPTIONAL_HOOKS[role])}"
             for role in sorted(REQUIRED_HOOKS)]
    return block("contract", [
        f"ADAPTER = {{'contract': {CURRENT_CONTRACT}, 'role': 'source'|'target', 'provider': 'custom',",
        "  'title': ..., 'description': ..., 'connection': {...}, 'options': {...},",
        "  'capabilities': {...} (targets only), 'reads': [...], 'hooks': {...}}",
        "Hooks per role:", *roles,
        f"Option types: {[t for t in OPTION_TYPES if t != 'local_folder']}. An option is",
        "{'type', 'label', 'help'?, 'required'?, 'default'?, 'choices'? (select),",
        "'create'? (remote_collection: offer a new one by name)}. remote_folder and",
        "remote_collection options are filled from the 'choices' hook, which is then required.",
        "ctx = {'options': {...}, 'state': {...}, 'run': {'preview', 'run_id', 'pass'}}.",
        f"Errors: return {{'error': {{'code': c, 'message': ...}}}} with c in {list(ERROR_CODES)}",
        "(auth = the account was refused; rate_limited may carry retry_after_s; fatal stops",
        "the sync; retry_later tries again later).",
        "",
        "SOURCE hooks:",
        "- list_page(cursor, ctx) -> {'records': [...], 'next_cursor': str|None,",
        "  'checkpoint'?: str, 'state_patch'?: {...}}. cursor is None on the first page.",
        "  A record: {'source_id' (stable, required), 'filename' (required),",
        "  'folder'? (relative path for keep-the-source's-folders), 'download': {'url',",
        "  'headers'?}, 'size'? (int), 'mime'?, 'hash'? {'algo': 'sha1', 'value': lowercase",
        "  hex} (lets Yaffo link photos it already has without downloading), 'taken_at'?",
        "  {'local'|'utc'|'offset'}, 'location'? {'lat', 'lon'}, 'updated_at'? (any string",
        "  that changes when the file changes), 'deleted'? (a tombstone)}.",
        "- choices(option, parent_id, cursor, ctx) -> {'items': [{'id', 'label',",
        "  'has_children'?, 'suggested'?}], 'next_cursor'}.",
        "- resolve_download(record, ctx) -> {'download': {...}} when list_page can't",
        "  give a download URL up front.",
        "",
        "TARGET hooks: push gets a batch of items {'key', 'media_item_id', 'target' (the",
        "routing name, e.g. an album title), 'caption', 'filename', 'mime', 'size',",
        "'taken_at'}. Upload with web_upload_media(CONNECTION, method, url,",
        "item['media_item_id'], ...); you never get the bytes or a path. Every result names",
        "its item's 'key':",
        "- push(batch, ctx) -> {'results': [{'key', 'remote_id', 'details'?} | {'key',",
        "  'error'}], 'state_patch'?}",
        "- remove(items, ctx) / update(items, ctx) -> {'results': [{'key'}...]}",
        "- list_remote(cursor, ctx) -> {'items': [{'remote_id', 'target', 'filename'?,",
        "  'details'?}], 'next_cursor'}: what the service holds, for crash recovery.",
        "capabilities: {'max_batch', 'remove', 'update', 'reconcile' (list_remote),",
        "'targets': 'one'|'many', 'accepts'?: {'types': [mime...], 'max_bytes'}} — each",
        "true capability needs its hook and each hook its capability.",
        "Removing: only ever take away what Yaffo itself put there.",
    ])


def _example() -> str:
    code = (PRESETS / "immich_source.star").read_text(encoding="utf-8")
    code = code.replace('"provider": "immich",\n', '"provider": "custom",\n    "connection": {"auth_kind": "api_key", '
                        '"instance": ["base_url"], "api_key": {"in": "header", "name": "x-api-key"},\n'
                        '                   "check": {"method": "GET", "url": "{base_url}/api/users/me", '
                        '"account_id": "id", "account_label": ["email"]},\n'
                        '                   "key_label": "API key", "key_help": "Account Settings › API Keys"},\n')
    return block("example", ["A complete source adapter (Immich, self-hosted, API key):", block("code", code.splitlines())])


def _target_example() -> str:
    code = (EXAMPLES / "flickr_target.star").read_text(encoding="utf-8")
    return block("example", ["A complete target adapter (Flickr sets, OAuth 1.0a with the user's own app; Flickr "
                             "answers failures as HTTP 200 with stat 'fail', and uploads in XML):",
                             block("code", code.splitlines())])


def build_adapter_builder_system_prompt() -> str:
    return "\n\n".join([_role(), _workflow(), _language(), _host_api(), _urls(), _connection(), _contract(),
                        _example(), _target_example(), response_language_block()])

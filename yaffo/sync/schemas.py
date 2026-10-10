"""The JSON stored for sync automations, as named shapes.

`automations.sync` holds {"config": SyncConfig, "runtime": SyncRuntime}. `config` is
written only by the UI (setup and consent); `runtime` only by the engine, in the same
transaction as the ledger rows it describes. Both are validated with jsonschema on
every load and write, and loading invalid JSON raises SyncSchemaError so the engine
pauses the sync (`invalid_config`) instead of running on a guess.

See docs/development/automation-sync-adapters.md (Data model).
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any

from jsonschema import Draft202012Validator

from yaffo.db.models import SYNC_ROLE_SOURCE, SYNC_ROLE_TARGET

SELECTION_ALBUM = "album"
SELECTION_QUERY = "query"
SELECTION_SCRIPT = "script"
SELECTION_KINDS = (SELECTION_ALBUM, SELECTION_QUERY, SELECTION_SCRIPT)

RUNTIME_READY = "ready"
RUNTIME_NEEDS_CONSENT = "needs_consent"
RUNTIME_NEEDS_SIGN_IN = "needs_sign_in"
RUNTIME_PAUSED = "paused"
RUNTIME_INVALID_CONFIG = "invalid_config"
RUNTIME_STATUSES = (RUNTIME_READY, RUNTIME_NEEDS_CONSENT, RUNTIME_NEEDS_SIGN_IN,
                    RUNTIME_PAUSED, RUNTIME_INVALID_CONFIG)

# Why a sync is paused (runtime.pause_code), shown as "action needed".
PAUSE_SELECTION_MISSING = "selection_missing"
PAUSE_DESTINATION_MISSING = "destination_missing"
PAUSE_OPTION_MISSING = "option_missing"

PASS_BACKFILL = "backfill"
PASS_INCREMENTAL = "incremental"
PASS_RECONCILE = "reconcile"

CURSOR_LIST = "list"
CURSOR_RECONCILE = "reconcile"

STATE_MAX_BYTES = 64 * 1024


class SyncSchemaError(ValueError):
    """Stored or submitted sync JSON doesn't match its schema."""


_NULLABLE_STRING = {"type": ["string", "null"]}

SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind"],
    "properties": {
        "kind": {"enum": list(SELECTION_KINDS)},
        "album_id": {"type": ["integer", "null"]},
        "query": {"type": ["object", "null"]},
    },
    "allOf": [
        {"if": {"properties": {"kind": {"const": SELECTION_ALBUM}}},
         "then": {"required": ["album_id"], "properties": {"album_id": {"type": "integer"}}}},
        {"if": {"properties": {"kind": {"const": SELECTION_QUERY}}},
         "then": {"required": ["query"], "properties": {"query": {"type": "object"}}}},
    ],
}

# How a source sync arranges new files under its import root.
LAYOUT_SOURCE_FOLDERS = "source_folders"   # mirror the folders under the chosen source folder
LAYOUT_YEAR_MONTH = "year_month"           # YYYY/MM of the capture date
LAYOUT_YEAR = "year"                       # YYYY of the capture date
LAYOUT_FLAT = "flat"                       # all in the import root
IMPORT_LAYOUTS = (LAYOUT_SOURCE_FOLDERS, LAYOUT_YEAR_MONTH, LAYOUT_YEAR, LAYOUT_FLAT)
DEFAULT_IMPORT_LAYOUT = LAYOUT_SOURCE_FOLDERS

IMPORT_ROOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["media_dir_id", "subpath", "layout"],
    "properties": {
        "media_dir_id": {"type": "string", "minLength": 1},
        "subpath": {"type": "string"},
        "layout": {"enum": list(IMPORT_LAYOUTS)},
    },
}

CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["role", "adapter_id", "connection_key", "scopes_needed", "options",
                 "import_root", "selection", "consent_hash", "consented_at"],
    "properties": {
        "role": {"enum": [SYNC_ROLE_SOURCE, SYNC_ROLE_TARGET]},
        "adapter_id": {"type": "integer"},
        "connection_key": _NULLABLE_STRING,
        "scopes_needed": {"type": "array", "items": {"type": "string"}},
        "options": {"type": "object"},
        "import_root": {"oneOf": [{"type": "null"}, IMPORT_ROOT_SCHEMA]},
        "selection": {"oneOf": [{"type": "null"}, SELECTION_SCHEMA]},
        "consent_hash": _NULLABLE_STRING,
        "consented_at": _NULLABLE_STRING,
    },
}

CURSOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pass_kind", "next_cursor", "checkpoint", "pass_started_at", "last_completed_at"],
    "properties": {
        "pass_kind": {"enum": [PASS_BACKFILL, PASS_INCREMENTAL, PASS_RECONCILE, None]},
        "next_cursor": _NULLABLE_STRING,
        "checkpoint": _NULLABLE_STRING,
        "pass_started_at": _NULLABLE_STRING,
        "last_completed_at": _NULLABLE_STRING,
    },
}

RUNTIME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "pause_code", "state", "cursors", "last_run_at"],
    "properties": {
        "status": {"enum": list(RUNTIME_STATUSES)},
        "pause_code": _NULLABLE_STRING,
        "state": {"type": "object"},
        "cursors": {"type": "object", "additionalProperties": CURSOR_SCHEMA},
        "last_run_at": _NULLABLE_STRING,
        "followups": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
}

SYNC_COLUMN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["config", "runtime"],
    "properties": {"config": CONFIG_SCHEMA, "runtime": RUNTIME_SCHEMA},
}

_VALIDATORS = {
    id(schema): Draft202012Validator(schema)
    for schema in (SELECTION_SCHEMA, CONFIG_SCHEMA, CURSOR_SCHEMA, RUNTIME_SCHEMA, SYNC_COLUMN_SCHEMA)
}


def validate(schema: dict[str, Any], data: Any, what: str) -> None:
    errors = sorted(_VALIDATORS[id(schema)].iter_errors(data), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(part) for part in first.absolute_path) or "(root)"
        raise SyncSchemaError(f"invalid {what} at {where}: {first.message}")


@dataclass(frozen=True)
class Selection:
    kind: str
    album_id: int | None = None
    query: dict | None = None

    @classmethod
    def from_dict(cls, data: dict) -> Selection:
        validate(SELECTION_SCHEMA, data, "selection")
        return cls(kind=data["kind"], album_id=data.get("album_id"), query=data.get("query"))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "album_id": self.album_id, "query": self.query}


@dataclass(frozen=True)
class SyncConfig:
    role: str
    adapter_id: int
    connection_key: str | None
    scopes_needed: list[str]
    options: dict
    import_root: dict | None
    selection: Selection | None
    consent_hash: str | None
    consented_at: str | None

    @classmethod
    def from_dict(cls, data: dict) -> SyncConfig:
        validate(CONFIG_SCHEMA, data, "sync config")
        selection = data["selection"]
        return cls(
            role=data["role"],
            adapter_id=data["adapter_id"],
            connection_key=data["connection_key"],
            scopes_needed=list(data["scopes_needed"]),
            options=dict(data["options"]),
            import_root=dict(data["import_root"]) if data["import_root"] else None,
            selection=Selection.from_dict(selection) if selection else None,
            consent_hash=data["consent_hash"],
            consented_at=data["consented_at"],
        )

    def to_dict(self) -> dict:
        data = {
            "role": self.role,
            "adapter_id": self.adapter_id,
            "connection_key": self.connection_key,
            "scopes_needed": list(self.scopes_needed),
            "options": dict(self.options),
            "import_root": dict(self.import_root) if self.import_root else None,
            "selection": self.selection.to_dict() if self.selection else None,
            "consent_hash": self.consent_hash,
            "consented_at": self.consented_at,
        }
        validate(CONFIG_SCHEMA, data, "sync config")
        return data


@dataclass(frozen=True)
class SyncCursor:
    pass_kind: str | None = None
    next_cursor: str | None = None
    checkpoint: str | None = None
    pass_started_at: str | None = None
    last_completed_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> SyncCursor:
        validate(CURSOR_SCHEMA, data, "sync cursor")
        return cls(**data)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class SyncRuntime:
    status: str = RUNTIME_NEEDS_CONSENT
    pause_code: str | None = None
    state: dict = field(default_factory=dict)
    cursors: dict[str, SyncCursor] = field(default_factory=dict)
    last_run_at: str | None = None
    # sha256 of the stored cursor and checkpoint when they're URLs a granted call
    # returned, so the next run may fetch them (the follow-up URL rule, see web.py).
    followups: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> SyncRuntime:
        validate(RUNTIME_SCHEMA, data, "sync runtime")
        return cls(
            status=data["status"],
            pause_code=data["pause_code"],
            state=dict(data["state"]),
            cursors={name: SyncCursor.from_dict(c) for name, c in data["cursors"].items()},
            last_run_at=data["last_run_at"],
            followups=list(data.get("followups") or []),
        )

    def to_dict(self) -> dict:
        data = {
            "status": self.status,
            "pause_code": self.pause_code,
            "state": dict(self.state),
            "cursors": {name: c.to_dict() for name, c in self.cursors.items()},
            "last_run_at": self.last_run_at,
            "followups": list(self.followups),
        }
        validate(RUNTIME_SCHEMA, data, "sync runtime")
        return data

    def cursor(self, name: str) -> SyncCursor:
        return self.cursors.get(name, SyncCursor())

    def with_cursor(self, name: str, cursor: SyncCursor) -> SyncRuntime:
        return replace(self, cursors={**self.cursors, name: cursor})


@dataclass(frozen=True)
class SyncColumn:
    """The whole `automations.sync` value."""
    config: SyncConfig
    runtime: SyncRuntime

    @classmethod
    def from_dict(cls, data: Any) -> SyncColumn:
        if not isinstance(data, dict):
            raise SyncSchemaError("sync column is not an object")
        validate(SYNC_COLUMN_SCHEMA, data, "sync column")
        return cls(config=SyncConfig.from_dict(data["config"]),
                   runtime=SyncRuntime.from_dict(data["runtime"]))

    def to_dict(self) -> dict:
        return {"config": self.config.to_dict(), "runtime": self.runtime.to_dict()}


# ---- connections and adapters ------------------------------------------------

CONNECTION_READY = "ready"
CONNECTION_NEEDS_SIGN_IN = "needs_sign_in"

GRANT_ENDPOINT = "endpoint"
GRANT_FOLLOWUP = "followup"
RISK_READ = "read"
RISK_WRITE = "write"
RISK_SHARES_EXTERNALLY = "shares_externally"

CONNECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["provider", "account_id", "account_label", "scopes", "status", "secret_ref", "instance",
                 "refreshing_until"],
    "properties": {
        "provider": {"type": "string", "minLength": 1},
        "account_id": {"type": "string", "minLength": 1},
        "account_label": _NULLABLE_STRING,
        "scopes": {"type": "array", "items": {"type": "string"}},
        "status": {"enum": [CONNECTION_READY, CONNECTION_NEEDS_SIGN_IN]},
        "secret_ref": {"type": "string", "minLength": 1},
        "instance": {"type": "object"},
        "refreshing_until": _NULLABLE_STRING,
    },
}

GRANT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["method", "url_pattern", "grant_kind", "followup_of", "risk", "granted_code_hash", "granted_at"],
    "properties": {
        "method": {"type": "string", "minLength": 1},
        "url_pattern": _NULLABLE_STRING,
        "grant_kind": {"enum": [GRANT_ENDPOINT, GRANT_FOLLOWUP]},
        "followup_of": _NULLABLE_STRING,
        "risk": {"enum": [RISK_READ, RISK_WRITE, RISK_SHARES_EXTERNALLY]},
        "granted_code_hash": {"type": "string"},
        "granted_at": {"type": "string"},
    },
}

MANIFEST_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["method", "url_pattern", "grant_kind", "risk", "line"],
    "properties": {
        "method": {"type": "string"},
        "url_pattern": _NULLABLE_STRING,
        "grant_kind": {"enum": [GRANT_ENDPOINT, GRANT_FOLLOWUP]},
        "risk": {"enum": [RISK_READ, RISK_WRITE, RISK_SHARES_EXTERNALLY]},
        "line": {"type": "integer"},
    },
}

ADAPTER_VERSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code_hash", "contract", "metadata", "manifest"],
    "properties": {
        "code_hash": {"type": "string", "minLength": 1},
        "contract": {"type": "integer"},
        "metadata": {"type": "object"},
        "manifest": {"type": "array", "items": MANIFEST_ENTRY_SCHEMA},
    },
}

for _schema in (CONNECTION_SCHEMA, GRANT_SCHEMA, ADAPTER_VERSION_SCHEMA):
    _VALIDATORS[id(_schema)] = Draft202012Validator(_schema)


@dataclass(frozen=True)
class SyncConnection:
    """An `application_settings` value for a `sync_connection:…` row: a signed-in
    account at a provider, shared by every sync that uses that account. The secret
    itself is in the keychain, named by `secret_ref`."""
    provider: str
    account_id: str
    account_label: str | None
    scopes: list[str]
    status: str
    secret_ref: str
    instance: dict = field(default_factory=dict)
    refreshing_until: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> SyncConnection:
        validate(CONNECTION_SCHEMA, data, "sync connection")
        return cls(provider=data["provider"], account_id=data["account_id"], account_label=data["account_label"],
                   scopes=list(data["scopes"]), status=data["status"], secret_ref=data["secret_ref"],
                   instance=dict(data["instance"]), refreshing_until=data["refreshing_until"])

    def to_dict(self) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["scopes"] = list(self.scopes)
        data["instance"] = dict(self.instance)
        validate(CONNECTION_SCHEMA, data, "sync connection")
        return data


@dataclass(frozen=True)
class WebGrant:
    """One authorized endpoint (or follow-up URL kind) of an adapter, pinned to the
    code it was approved for."""
    method: str
    url_pattern: str | None
    grant_kind: str
    followup_of: str | None
    risk: str
    granted_code_hash: str
    granted_at: str

    @classmethod
    def from_dict(cls, data: Any) -> WebGrant:
        validate(GRANT_SCHEMA, data, "web grant")
        return cls(**data)

    def to_dict(self) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        validate(GRANT_SCHEMA, data, "web grant")
        return data


@dataclass(frozen=True)
class AdapterVersion:
    """`sync_adapters.working` / `.published`: what the static check extracted from
    the matching code column."""
    code_hash: str
    contract: int
    metadata: dict
    manifest: list[dict]

    @classmethod
    def from_dict(cls, data: Any) -> AdapterVersion:
        validate(ADAPTER_VERSION_SCHEMA, data, "adapter version")
        return cls(code_hash=data["code_hash"], contract=data["contract"], metadata=dict(data["metadata"]),
                   manifest=[dict(entry) for entry in data["manifest"]])

    def to_dict(self) -> dict:
        data = {"code_hash": self.code_hash, "contract": self.contract, "metadata": dict(self.metadata),
                "manifest": [dict(entry) for entry in self.manifest]}
        validate(ADAPTER_VERSION_SCHEMA, data, "adapter version")
        return data

"""Connection secrets (tokens, keys) in the OS keychain, never in the database.

Each connection's `secret_ref` names one keychain entry under the "yaffo" service,
holding a JSON object (an OAuth token set, an API key, ...). Only the host reads it:
it never reaches scripts, logs, previews, or the LLM.

`YAFFO_SYNC_SECRETS_FILE` (the UI-test sandbox and development only) keeps the
entries in a JSON file instead, so test sign-ins leave the real keychain clean. It's
a file rather than memory because the web app and the queue's workers are separate
processes and both need the token.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from yaffo.logging_config import get_logger

logger = get_logger(__name__)

SERVICE = "yaffo"
SECRETS_FILE_ENV = "YAFFO_SYNC_SECRETS_FILE"

_file_lock = threading.Lock()


class SecretUnavailable(Exception):
    """The keychain can't be read or written."""


def _file() -> Path | None:
    value = os.environ.get(SECRETS_FILE_ENV)
    return Path(value) if value else None


def _read_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def load_secret(ref: str) -> dict[str, Any] | None:
    path = _file()
    if path is not None:
        with _file_lock:
            return _read_file(path).get(ref)
    try:
        raw = keyring.get_password(SERVICE, ref)
    except KeyringError as e:
        raise SecretUnavailable(str(e)) from e
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        logger.warning(f"keychain entry {ref} isn't JSON; ignoring it")
        return None
    return value if isinstance(value, dict) else None


def store_secret(ref: str, value: dict[str, Any]) -> None:
    path = _file()
    if path is not None:
        with _file_lock:
            data = _read_file(path)
            data[ref] = value
            _write_file(path, data)
        return
    try:
        keyring.set_password(SERVICE, ref, json.dumps(value))
    except KeyringError as e:
        raise SecretUnavailable(str(e)) from e


def delete_secret(ref: str) -> None:
    path = _file()
    if path is not None:
        with _file_lock:
            data = _read_file(path)
            if data.pop(ref, None) is not None:
                _write_file(path, data)
        return
    try:
        keyring.delete_password(SERVICE, ref)
    except PasswordDeleteError:
        pass
    except KeyringError as e:
        logger.warning(f"couldn't delete keychain entry {ref}: {e}")

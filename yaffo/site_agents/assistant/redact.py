"""One redaction pass over everything a diagnostic tool or script sends the model.

Applied to the model text of every assistant tool result (and so to what the
chat's expanded activity line shows, which is the same text): the user sees
exactly what left the machine.

- a configured folder (each media folder, the thumbnail folder, the data folder)
  becomes a label, e.g. `[media folder <id>]`, keeping the path inside it, so
  where the library lives and what its folders are called never leave the machine
- the rest of the home directory becomes `~`
- API-key-like tokens and `key=value` secrets are masked
- email addresses are masked
- GPS coordinates are rounded to one decimal place (about 11 km)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from yaffo.common import ROOT_DIR
from yaffo.db.repositories.media_dir_repository import get_media_dir_entries
from yaffo.utils.settings import get_thumbnail_dir


MASK = "[redacted]"
EMAIL_MASK = "[email]"

_KEY_PATTERNS = (
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}"),
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-~+/]{12,}=*")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b((?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd)\"?\s*[:=]\s*\"?)"
    r"([^\s\"',}]{4,})"
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# "44.428013, -110.588455" -- a pair with enough precision to be a real position.
_COORDINATE_PAIR = re.compile(r"(?<![\w.])(-?\d{1,3}\.\d{3,})(\s*,\s*)(-?\d{1,3}\.\d{3,})(?![\w.])")
# "latitude": 44.428013 / lon=-110.58 (JSON dumps and key=value log lines).
_COORDINATE_FIELD = re.compile(
    r"(?i)(\b(?:latitude|longitude|lat|lon|lng)\"?\s*[:=]\s*)(-?\d{1,3}\.\d{2,})"
)


def _round(value: str) -> str:
    return f"{float(value):.1f}"


def _path_forms(path: Path) -> set[str]:
    """The path as configured and resolved (symlinks, /private on macOS), without a
    trailing separator; never a bare filesystem root."""
    forms = {str(path)}
    try:
        forms.add(str(path.resolve()))
    except OSError:
        pass
    return {f.rstrip("/\\") for f in forms if f and f.rstrip("/\\")}


def _path_pattern(path_text: str) -> re.Pattern:
    # Only whole folder names match (/Users/alex, not /Users/alexandra).
    return re.compile(re.escape(path_text) + r"(?![\w.\-])")


class Redactor:
    """`roots` maps a label to a folder: the folder becomes `[label]`. `home`
    (default: the user's home) is replaced by `~` wherever no root matched."""

    def __init__(self, *, home: Optional[Path] = None, roots: Optional[dict[str, Path]] = None):
        # Longest first everywhere, so a media folder inside the data folder gets its
        # own label and /private/var/... wins over /var/....
        replacements = [
            (form, f"[{label}]")
            for label, path in (roots or {}).items()
            for form in _path_forms(path)
        ]
        replacements.sort(key=lambda item: len(item[0]), reverse=True)
        self._roots = [(_path_pattern(form), label) for form, label in replacements]
        self._homes = [
            _path_pattern(h) for h in sorted(_path_forms(home or Path.home()), key=len, reverse=True)
        ]

    def __call__(self, text: str) -> str:
        return self.redact(text)

    def redact(self, text: str) -> str:
        if not text:
            return text
        for pattern, label in self._roots:
            text = pattern.sub(lambda _m, label=label: label, text)
        for home in self._homes:
            text = home.sub("~", text)
        for pattern in _KEY_PATTERNS:
            text = pattern.sub(MASK, text)
        text = _BEARER.sub(lambda m: m.group(1) + MASK, text)
        text = _SECRET_ASSIGNMENT.sub(lambda m: m.group(1) + MASK, text)
        text = _EMAIL.sub(EMAIL_MASK, text)
        text = _COORDINATE_PAIR.sub(lambda m: _round(m.group(1)) + m.group(2) + _round(m.group(3)), text)
        text = _COORDINATE_FIELD.sub(lambda m: m.group(1) + _round(m.group(2)), text)
        return text


def install_roots(session: Session) -> dict[str, Path]:
    """This install's configured folders, by the label the model sees. A media
    folder's label carries its id, which the file tools take."""
    roots: dict[str, Path] = {"data folder": Path(ROOT_DIR)}
    thumbnail_dir = get_thumbnail_dir(session)
    if thumbnail_dir is not None:
        roots["thumbnail folder"] = thumbnail_dir
    for entry in get_media_dir_entries(session):
        roots[f"media folder {entry.id}"] = entry.path
    return roots


def redactor_for(session: Session) -> Redactor:
    """The redactor for one assistant run: this install's folders become labels."""
    return Redactor(roots=install_roots(session))

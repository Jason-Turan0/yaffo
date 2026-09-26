"""One redaction pass over everything a diagnostic tool or script sends the model.

Applied to the model text of every assistant tool result (and so to what the
chat's expanded activity line shows, which is the same text): the user sees
exactly what left the machine.

- the home directory becomes `~`
- API-key-like tokens and `key=value` secrets are masked
- email addresses are masked
- GPS coordinates are rounded to one decimal place (about 11 km)
- optionally, people names become `Person #<id>`
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from yaffo.db.models import Person

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


class Redactor:
    """`home` is replaced by `~`; `people` maps person id → name when names are
    redacted (None leaves names alone)."""

    def __init__(self, *, home: Optional[Path] = None, people: Optional[dict[int, str]] = None):
        homes = {str(home or Path.home())}
        try:
            homes.add(str((home or Path.home()).resolve()))
        except OSError:
            pass
        # Longest first, so /private/var/... wins over /var/...; only whole folder
        # names match (/Users/alex, not /Users/alexandra).
        self._homes = [
            re.compile(re.escape(h) + r"(?![\w.\-])")
            for h in sorted((h.rstrip("/\\") for h in homes if h and h not in ("/", "\\")), key=len, reverse=True)
        ]
        self._people: list[tuple[re.Pattern, str]] = []
        for person_id, name in sorted((people or {}).items(), key=lambda item: -len(item[1] or "")):
            name = (name or "").strip()
            if len(name) < 2:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
            self._people.append((pattern, f"Person #{person_id}"))

    def __call__(self, text: str) -> str:
        return self.redact(text)

    def redact(self, text: str) -> str:
        if not text:
            return text
        for home in self._homes:
            text = home.sub("~", text)
        for pattern in _KEY_PATTERNS:
            text = pattern.sub(MASK, text)
        text = _BEARER.sub(lambda m: m.group(1) + MASK, text)
        text = _SECRET_ASSIGNMENT.sub(lambda m: m.group(1) + MASK, text)
        text = _EMAIL.sub(EMAIL_MASK, text)
        text = _COORDINATE_PAIR.sub(lambda m: _round(m.group(1)) + m.group(2) + _round(m.group(3)), text)
        text = _COORDINATE_FIELD.sub(lambda m: m.group(1) + _round(m.group(2)), text)
        for pattern, replacement in self._people:
            text = pattern.sub(replacement, text)
        return text


def redactor_for(session: Session, *, redact_people: bool) -> Redactor:
    people = None
    if redact_people:
        people = {pid: name for pid, name in session.query(Person.id, Person.name).all() if name}
    return Redactor(people=people)

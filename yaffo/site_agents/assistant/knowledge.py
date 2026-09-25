"""The assistant's bundled docs: loading, keyword search, and page/section reads.

The bundle is built from `docs/` by `scripts/build_assistant_knowledge.py` and
shipped with the app (see docs/development/ai-assistant.md, *Knowledge bundle*).
Search is offline BM25 over the sections, with the heading and page title weighted
above the body, so no index file or search dependency is needed.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from yaffo.common import BUNDLE_ROOT

KNOWLEDGE_DIR = BUNDLE_ROOT / "yaffo" / "assistant_knowledge"
SCOPES = ("guide", "development")

# BM25 parameters (the usual defaults) and how much a heading/title term counts
# relative to a body term.
_K1 = 1.5
_B = 0.75
_TITLE_WEIGHT = 3
# The user guide is written for the people asking; development notes win only when
# they match clearly better.
_GUIDE_BOOST = 1.3

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from how i if in into is it its "
    "me my of on or so that the their them then there these this to was what when "
    "where which who why will with you your".split()
)


@dataclass(frozen=True)
class DocSection:
    id: str
    scope: str
    path: str
    page_title: str
    heading: str
    level: int
    anchor: str
    url: str
    text: str


@dataclass(frozen=True)
class SearchHit:
    section: DocSection
    score: float
    snippet: str


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class KnowledgeBase:
    """An in-memory index over the bundled sections."""

    def __init__(self, sections: list[DocSection]):
        self.sections = sections
        self._by_path: dict[str, list[DocSection]] = {}
        for section in sections:
            self._by_path.setdefault(section.path, []).append(section)
        self._term_counts: list[Counter[str]] = []
        self._lengths: list[int] = []
        document_frequency: Counter[str] = Counter()
        for section in sections:
            terms = tokenize(section.text)
            terms += tokenize(f"{section.heading} {section.page_title}") * _TITLE_WEIGHT
            counts = Counter(terms)
            self._term_counts.append(counts)
            self._lengths.append(len(terms))
            document_frequency.update(counts.keys())
        self._average_length = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        total = len(sections)
        self._idf = {
            term: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
            for term, freq in document_frequency.items()
        }

    @classmethod
    def load(cls, directory: Path = KNOWLEDGE_DIR) -> "KnowledgeBase":
        path = directory / "sections.jsonl"
        sections = []
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                sections = [DocSection(**json.loads(line)) for line in handle if line.strip()]
        return cls(sections)

    def search(self, query: str, scope: Optional[str] = None, limit: int = 6) -> list[SearchHit]:
        terms = [t for t in dict.fromkeys(tokenize(query)) if t in self._idf]
        if not terms:
            return []
        scored: list[tuple[float, int]] = []
        for index, section in enumerate(self.sections):
            if scope and section.scope != scope:
                continue
            counts = self._term_counts[index]
            length_norm = _K1 * (1 - _B + _B * self._lengths[index] / (self._average_length or 1))
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if tf:
                    score += self._idf[term] * tf * (_K1 + 1) / (tf + length_norm)
            if score > 0:
                if section.scope == "guide":
                    score *= _GUIDE_BOOST
                scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            SearchHit(self.sections[i], round(score, 3), _snippet(self.sections[i].text, terms))
            for score, i in scored[:limit]
        ]

    def page(self, path: str) -> list[DocSection]:
        return list(self._by_path.get(path, []))

    def section(self, path: str, anchor: str) -> Optional[DocSection]:
        return next((s for s in self._by_path.get(path, []) if s.anchor == anchor), None)


def _snippet(text: str, terms: list[str], width: int = 240) -> str:
    """A window of the text around the first matched term, on word boundaries."""
    flat = " ".join(text.split())
    lowered = flat.lower()
    positions = [p for p in (lowered.find(t) for t in terms) if p >= 0]
    start = max(0, min(positions) - width // 3) if positions else 0
    if start:
        start = flat.find(" ", start) + 1 or start
    excerpt = flat[start:start + width]
    if start + width < len(flat):
        excerpt = excerpt.rsplit(" ", 1)[0] + " …"
    return ("… " if start else "") + excerpt


@lru_cache(maxsize=1)
def knowledge_base() -> KnowledgeBase:
    """The bundled knowledge, loaded once per process."""
    return KnowledgeBase.load()

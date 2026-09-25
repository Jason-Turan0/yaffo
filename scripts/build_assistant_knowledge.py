"""Build the assistant's bundled knowledge from the docs.

Reads the user guide (`docs/index.md`, `docs/guide/**`) and the development notes
(`docs/development/**`), splits each page into sections at its headings, and writes
`yaffo/assistant_knowledge/sections.jsonl` plus `manifest.json`. The app ships these
files (package data / PyInstaller datas) because the docs themselves are not
packaged, and the assistant must answer offline from docs that match the running
version.

The manifest records a hash of the source docs; a test fails when it no longer
matches, so the bundle can't drift from `docs/`. Rebuild after editing docs:

    python -m scripts.build_assistant_knowledge
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
OUTPUT_DIR = ROOT / "yaffo" / "assistant_knowledge"
SECTIONS_FILE = "sections.jsonl"
MANIFEST_FILE = "manifest.json"

# Bump when the section format or cleaning rules change, so the freshness check
# asks for a rebuild even though no doc changed.
FORMAT_VERSION = 2

# Docs left out of the bundle. A proposal describes features that don't exist
# yet, and the assistant would present them as fact; include it once it becomes the
# design reference for shipped behavior.
EXCLUDED = frozenset({
    "development/ai-assistant.md",
})

SCOPE_GUIDE = "guide"
SCOPE_DEVELOPMENT = "development"

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)(\{[^}]*\})?")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_ATTR_LIST_RE = re.compile(r"\{[:#.][^}]*\}")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_ADMONITION_RE = re.compile(r'^(\s*)(?:!!!|\?\?\?\+?)\s*(\w+)(?:\s+"([^"]*)")?\s*$')
_SITE_URL_RE = re.compile(r"^site_url:\s*(\S+)\s*$", re.MULTILINE)
_INLINE_MARKUP_RE = re.compile(r"`|\*\*|(?<!\w)\*|\*(?!\w)")


@dataclass(frozen=True)
class Section:
    id: str            # "<path>#<anchor>", unique across the bundle
    scope: str         # SCOPE_GUIDE | SCOPE_DEVELOPMENT
    path: str          # docs-relative, e.g. "guide/start-here/getting-started.md"
    page_title: str
    heading: str
    level: int         # 1-3
    anchor: str        # MkDocs heading id ("" for a page's lead section)
    url: str           # published docs URL for the section
    text: str


def source_files(docs_dir: Path = DOCS_DIR) -> list[Path]:
    """The docs the bundle is built from, in a stable order."""
    files = [docs_dir / "index.md"]
    for sub in ("guide", "development"):
        files.extend(sorted((docs_dir / sub).rglob("*.md")))
    return [
        f for f in files
        if f.is_file() and f.relative_to(docs_dir).as_posix() not in EXCLUDED
    ]


def source_hash(docs_dir: Path = DOCS_DIR) -> str:
    """Hash of every source doc (path + bytes) and the format version."""
    digest = hashlib.sha256(f"format:{FORMAT_VERSION}\n".encode())
    for path in source_files(docs_dir):
        digest.update(path.relative_to(docs_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def slugify(value: str) -> str:
    """MkDocs' heading id (Python-Markdown's toc slugify), without its dependency."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


def plain_heading(heading: str) -> str:
    """Heading text as rendered: code backticks and bold/italic markers removed."""
    return _INLINE_MARKUP_RE.sub("", heading).strip()


def page_url(site_url: str, rel_path: str) -> str:
    """The published URL of a page under MkDocs' default directory URLs."""
    base = site_url.rstrip("/") + "/"
    stem = rel_path[:-len(".md")]
    if stem == "index":
        return base
    if stem.endswith("/index"):
        return base + stem[:-len("index")]
    return base + stem + "/"


def clean_text(markdown: str) -> str:
    """Markdown trimmed to what helps answer questions: images, attribute lists
    and HTML tags dropped, links reduced to their text, admonitions to a label."""
    lines = []
    for line in markdown.splitlines():
        admonition = _ADMONITION_RE.match(line)
        if admonition:
            indent, kind, title = admonition.groups()
            lines.append(f"{indent}{kind.capitalize()}: {title}" if title else f"{indent}{kind.capitalize()}:")
            continue
        line = _IMAGE_RE.sub("", line)
        line = _LINK_RE.sub(r"\1", line)
        line = _ATTR_LIST_RE.sub("", line)
        line = _HTML_TAG_RE.sub("", line)
        lines.append(line.rstrip())
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_page(markdown: str) -> list[tuple[int, str, list[str]]]:
    """(level, heading, body lines) per heading of level 1-3, ignoring `#` lines
    inside fenced code blocks. Text before the first heading joins a level-0 lead."""
    sections: list[tuple[int, str, list[str]]] = [(0, "", [])]
    in_fence = False
    for line in markdown.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING_RE.match(line)
        if heading:
            sections.append((len(heading.group(1)), heading.group(2).strip(), []))
        else:
            sections[-1][2].append(line)
    return sections


def build_sections(docs_dir: Path = DOCS_DIR, site_url: str = "") -> list[Section]:
    sections: list[Section] = []
    for path in source_files(docs_dir):
        rel_path = path.relative_to(docs_dir).as_posix()
        scope = SCOPE_DEVELOPMENT if rel_path.startswith("development/") else SCOPE_GUIDE
        url = page_url(site_url, rel_path)
        parts = split_page(path.read_text(encoding="utf-8"))
        page_title = plain_heading(next((h for level, h, _ in parts if level == 1), Path(rel_path).stem))
        seen: dict[str, int] = {}
        for level, heading, body in parts:
            text = clean_text("\n".join(body))
            if level == 0 and not text:
                continue
            anchor = ""
            if level > 1:
                base = slugify(heading)
                count = seen.get(base, 0)
                seen[base] = count + 1
                anchor = base if count == 0 else f"{base}_{count}"
            if not text:
                continue  # a heading that only introduces its subsections
            sections.append(Section(
                id=f"{rel_path}#{anchor}",
                scope=scope,
                path=rel_path,
                page_title=page_title,
                heading=plain_heading(heading) or page_title,
                level=max(level, 1),
                anchor=anchor,
                url=f"{url}#{anchor}" if anchor else url,
                text=text,
            ))
    return sections


def read_site_url(root: Path = ROOT) -> str:
    match = _SITE_URL_RE.search((root / "mkdocs.yml").read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def build(docs_dir: Path = DOCS_DIR, output_dir: Path = OUTPUT_DIR) -> dict:
    site_url = read_site_url()
    sections = build_sections(docs_dir, site_url)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / SECTIONS_FILE).open("w", encoding="utf-8", newline="\n") as handle:
        for section in sections:
            handle.write(json.dumps(asdict(section), ensure_ascii=False) + "\n")
    manifest = {
        "format_version": FORMAT_VERSION,
        "source_hash": source_hash(docs_dir),
        "site_url": site_url,
        "section_count": len(sections),
    }
    (output_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    result = build()
    print(f"Wrote {result['section_count']} sections to {OUTPUT_DIR.relative_to(ROOT)}")

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from yaffo.common import BUNDLE_ROOT

ROOT = BUNDLE_ROOT
TEMPLATES_DIR = ROOT / "yaffo" / "templates"
JAVASCRIPT_DIR = ROOT / "yaffo" / "static"
BASELINE_PATH = ROOT / "tests" / "yaffo" / "i18n_hardcoded_baseline.json"

JINJA_COMMENT_RE = re.compile(r"{#.*?#}", re.DOTALL)
JINJA_TRANS_RE = re.compile(r"{%\s*trans\b.*?{%\s*endtrans\s*%}", re.DOTALL)
JINJA_TOKEN_RE = re.compile(r"{{.*?}}|{%.*?%}", re.DOTALL)
SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
ATTRIBUTE_RE = re.compile(
    r"""\b(?P<name>alt|aria-label|placeholder|title)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.IGNORECASE | re.DOTALL,
)
LETTER_RE = re.compile(r"[A-Za-z]")
WHITESPACE_RE = re.compile(r"\s+")

JS_LITERAL = r"""(?P<quote>["'`])(?P<value>(?:\\.|(?!\1).)*?)(?P=quote)"""
JS_HTML_TEMPLATE_RE = re.compile(r"`(?P<value>(?:\\.|[^`])*<[^`]+)`", re.DOTALL)

# String/template literals and comments, matched in ONE pass so a `//` or `/*`
# sitting inside a literal (e.g. a URL) is consumed as the literal and never seen as
# a comment. Only the comment groups are blanked (see _mask_js_comments); literals are
# kept verbatim. Regex literals aren't tokenized — a rare, accepted heuristic gap.
JS_STRING_OR_COMMENT_RE = re.compile(
    r"""
      "(?:\\.|[^"\\])*"            # double-quoted string
    | '(?:\\.|[^'\\])*'            # single-quoted string
    | `(?:\\.|[^`\\])*`            # template literal
    | (?P<block>/\*[\s\S]*?\*/)    # /* … */ block comment (incl. /** JSDoc */)
    | (?P<line>//[^\n]*)           # // line comment
    """,
    re.VERBOSE,
)
JS_PATTERNS = (
    (
        "javascript-notification",
        re.compile(
            rf"""\b(?:window\.)?notification\.(?:success|error|warning|info|show)\s*\(\s*{JS_LITERAL}""",
            re.DOTALL,
        ),
    ),
    (
        "javascript-dom-text",
        re.compile(
            rf"""\.(?:textContent|innerText|innerHTML|outerHTML|placeholder|title)\s*=\s*{JS_LITERAL}""",
            re.DOTALL,
        ),
    ),
    (
        "javascript-ui-option",
        re.compile(
            rf"""\b(?:title|message|confirmText|cancelText|placeholder|ariaLabel)\s*:\s*{JS_LITERAL}""",
            re.DOTALL,
        ),
    ),
    (
        "javascript-notification-fallback",
        re.compile(
            rf"""\b(?:window\.)?notification\.(?:success|error|warning|info|show)\s*\([^;\n]*?\|\|\s*{JS_LITERAL}""",
            re.DOTALL,
        ),
    ),
    (
        "javascript-dom-fallback",
        re.compile(
            rf"""\.(?:textContent|innerText|innerHTML|outerHTML|placeholder|title)\s*=[^;\n]*?\|\|\s*{JS_LITERAL}""",
            re.DOTALL,
        ),
    ),
)


@dataclass(frozen=True)
class HardcodedText:
    path: str
    line: int
    kind: str
    text: str

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(f"{self.kind}\0{self.text}".encode()).hexdigest()[:16]
        return f"{self.path}:{digest}"


def _normalize_text(value: str) -> str:
    decoded = html.unescape(value)
    without_markup = TAG_RE.sub(" ", decoded)
    return WHITESPACE_RE.sub(" ", without_markup).strip()


def _is_user_facing(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized or not LETTER_RE.search(normalized):
        return False
    if normalized.startswith(("/", "#", ".", "[", "{")):
        return False
    return True


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _mask_match(match: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", match.group())


def _mask_js_comments(source: str) -> str:
    """Blank out JS line/block comments (including JSDoc) while leaving string and
    template literals intact, so example snippets and `@param` docs written in comments
    aren't mistaken for hard-coded UI text. Length and newlines are preserved so
    reported line numbers stay accurate."""
    def replace(match: re.Match[str]) -> str:
        if match.lastgroup in ("block", "line"):
            return _mask_match(match)
        return match.group()
    return JS_STRING_OR_COMMENT_RE.sub(replace, source)


def scan_jinja(path: Path, root: Path = ROOT) -> list[HardcodedText]:
    source = path.read_text(encoding="utf-8")
    masked = JINJA_COMMENT_RE.sub(_mask_match, source)
    masked = JINJA_TRANS_RE.sub(_mask_match, masked)
    masked = SCRIPT_STYLE_RE.sub(_mask_match, masked)
    findings: list[HardcodedText] = []
    relative_path = path.relative_to(root).as_posix()

    for match in ATTRIBUTE_RE.finditer(masked):
        value = match.group("value")
        if "{{" in value or "{%" in value or not _is_user_facing(value):
            continue
        findings.append(HardcodedText(
            path=relative_path,
            line=_line_number(masked, match.start()),
            kind=f"jinja-attribute:{match.group('name').lower()}",
            text=_normalize_text(value),
        ))

    text_source = JINJA_TOKEN_RE.sub(_mask_match, masked)
    cursor = 0
    for tag in TAG_RE.finditer(text_source):
        value = text_source[cursor:tag.start()]
        if _is_user_facing(value):
            findings.append(HardcodedText(
                path=relative_path,
                line=_line_number(text_source, cursor),
                kind="jinja-text",
                text=_normalize_text(value),
            ))
        cursor = tag.end()
    trailing = text_source[cursor:]
    if _is_user_facing(trailing):
        findings.append(HardcodedText(
            path=relative_path,
            line=_line_number(text_source, cursor),
            kind="jinja-text",
            text=_normalize_text(trailing),
        ))
    return findings


def scan_javascript(path: Path, root: Path = ROOT) -> list[HardcodedText]:
    source = _mask_js_comments(path.read_text(encoding="utf-8"))
    relative_path = path.relative_to(root).as_posix()
    findings: list[HardcodedText] = []
    seen: set[tuple[int, str, str]] = set()
    for kind, pattern in JS_PATTERNS:
        for match in pattern.finditer(source):
            value = match.group("value")
            if kind == "javascript-dom-text" and "<" in value:
                continue
            if "${" in value:
                normalized = _normalize_text(re.sub(r"\$\{.*?}", " ", value))
            else:
                normalized = _normalize_text(value)
            if not _is_user_facing(normalized):
                continue
            identity = (match.start(), kind, normalized)
            if identity in seen:
                continue
            seen.add(identity)
            findings.append(HardcodedText(
                path=relative_path,
                line=_line_number(source, match.start()),
                kind=kind,
                text=normalized,
            ))
    for match in JS_HTML_TEMPLATE_RE.finditer(source):
        value = re.sub(r"\$\{.*?}", "", match.group("value"), flags=re.DOTALL)
        for attribute in ATTRIBUTE_RE.finditer(value):
            attribute_value = attribute.group("value")
            if not _is_user_facing(attribute_value):
                continue
            findings.append(HardcodedText(
                path=relative_path,
                line=_line_number(source, match.start() + attribute.start()),
                kind=f"javascript-html-attribute:{attribute.group('name').lower()}",
                text=_normalize_text(attribute_value),
            ))
        cursor = 0
        for tag in TAG_RE.finditer(value):
            text = value[cursor:tag.start()]
            if _is_user_facing(text):
                findings.append(HardcodedText(
                    path=relative_path,
                    line=_line_number(source, match.start() + cursor),
                    kind="javascript-html-text",
                    text=_normalize_text(text),
                ))
            cursor = tag.end()
        trailing = value[cursor:]
        if _is_user_facing(trailing):
            findings.append(HardcodedText(
                path=relative_path,
                line=_line_number(source, match.start() + cursor),
                kind="javascript-html-text",
                text=_normalize_text(trailing),
            ))
    return findings


def scan_sources(
    templates_dir: Path = TEMPLATES_DIR,
    javascript_dir: Path = JAVASCRIPT_DIR,
    root: Path = ROOT,
) -> list[HardcodedText]:
    findings = []
    for path in sorted(templates_dir.rglob("*.html")):
        findings.extend(scan_jinja(path, root))
    for path in sorted(javascript_dir.rglob("*.js")):
        if "vendor" in path.relative_to(javascript_dir).parts:
            continue
        findings.extend(scan_javascript(path, root))
    return sorted(findings, key=lambda item: (item.path, item.line, item.kind, item.text))


def fingerprint_counts(findings: list[HardcodedText]) -> Counter[str]:
    return Counter(finding.fingerprint for finding in findings)


def read_baseline(path: Path = BASELINE_PATH) -> Counter[str]:
    return Counter(json.loads(path.read_text(encoding="utf-8")))


def write_baseline(findings: list[HardcodedText], path: Path = BASELINE_PATH) -> None:
    counts = dict(sorted(fingerprint_counts(findings).items()))
    path.write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")


def new_findings(
    findings: list[HardcodedText],
    baseline: Counter[str],
) -> list[HardcodedText]:
    remaining = baseline.copy()
    added = []
    for finding in findings:
        if remaining[finding.fingerprint]:
            remaining[finding.fingerprint] -= 1
        else:
            added.append(finding)
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()
    findings = scan_sources()
    if args.write_baseline:
        write_baseline(findings)
        print(f"Wrote {len(fingerprint_counts(findings))} fingerprints to {BASELINE_PATH}")
        return
    added = new_findings(findings, read_baseline())
    if added:
        details = "\n".join(
            f"{finding.path}:{finding.line}: {finding.kind}: {finding.text}"
            for finding in added
        )
        raise SystemExit(f"New hard-coded user-facing text found:\n{details}")
    print("No new hard-coded user-facing text")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from babel.messages.catalog import Catalog, Message
from babel.messages.pofile import read_po, write_po
from deep_translator.exceptions import TooManyRequests, TranslationNotFound
from tqdm import tqdm

from scripts.i18n_llm_cli import CliOptions, CliUnavailableError, DEFAULT_TIMEOUT_SECONDS, translate_with_cli

from yaffo.common import BUNDLE_ROOT
from yaffo.logging_config import get_logger
logger = get_logger(__name__)

ROOT = BUNDLE_ROOT
POT_PATH = ROOT / "messages.pot"
TRANSLATIONS_DIR = ROOT / "yaffo" / "translations"
BROWSER_LOCALES_DIR = ROOT / "yaffo" / "static" / "locales"
ENGLISH_BROWSER_PATH = BROWSER_LOCALES_DIR / "en.json"
LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
I18NEXT_PLACEHOLDER_RE = re.compile(r"{{\s*([\w.-]+)\s*}}")
GETTEXT_PLACEHOLDER_RE = re.compile(r"%\(([\w.-]+)\)[#0 +\-]?\d*(?:\.\d+)?[a-zA-Z]")
HTML_TAG_RE = re.compile(r"</?[^>]+>")
BRACE_PLACEHOLDER_RE = re.compile(r"\{[\w.-]+\}")
# Google Translate allows 5 requests/second; stay under it and back off when throttled.
DEEP_TRANSLATOR_MIN_INTERVAL_SECONDS = 0.3
DEEP_TRANSLATOR_MAX_RETRIES = 5
# Rounds per batch before an entry whose translation keeps failing validation is skipped.
TRANSLATION_MAX_ATTEMPTS = 3
DEEP_TRANSLATOR_BACKOFF_SECONDS = 5.0
DEEP_TRANSLATOR_TARGETS = {
    "ar": "ar",
    "de": "de",
    "es": "es",
    "fr": "fr",
    "hi": "hi",
    "zh": "zh-CN",
}
PROTECTED_TEXT_RE = re.compile("|".join([
    I18NEXT_PLACEHOLDER_RE.pattern,
    GETTEXT_PLACEHOLDER_RE.pattern,
    HTML_TAG_RE.pattern,
    BRACE_PLACEHOLDER_RE.pattern,
]))


@dataclass(frozen=True)
class TranslationEntry:
    id: str
    source: str | list[str]
    context: str


def _validate_locale(locale: str) -> str:
    if not LOCALE_RE.fullmatch(locale):
        raise ValueError(f"Invalid locale: {locale}")
    return locale


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def flatten_json(value: dict, prefix: str = "") -> dict[str, str]:
    flattened: dict[str, str] = {}
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            flattened.update(flatten_json(child, path))
        elif isinstance(child, str):
            flattened[path] = child
        else:
            raise ValueError(f"Catalog value at {path} must be a string")
    return flattened


def _set_json_path(value: dict, path: str, translated: str) -> None:
    parts = path.split(".")
    node = value
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = translated


def _delete_json_path(value: dict, path: str) -> None:
    """Remove a flattened key, and any parent objects it leaves empty."""
    parts = path.split(".")
    parents = [value]
    for part in parts[:-1]:
        parents.append(parents[-1][part])
    del parents[-1][parts[-1]]
    for depth in range(len(parts) - 1, 0, -1):
        if parents[depth]:
            break
        del parents[depth - 1][parts[depth - 1]]


@dataclass(frozen=True)
class BrowserSync:
    added: list[str]
    pruned: list[str]


def sync_browser_locale(locale: str, prune: bool = False) -> BrowserSync:
    """Give a locale's browser catalog every English key (empty until translated).
    Keys English no longer has are an error unless `prune` removes them: dropping
    translations is only safe when the English key was removed on purpose."""
    locale = _validate_locale(locale)
    english = _read_json(ENGLISH_BROWSER_PATH)
    english_flat = flatten_json(english)
    target_path = BROWSER_LOCALES_DIR / f"{locale}.json"
    target = _read_json(target_path) if target_path.exists() else {}
    target_flat = flatten_json(target)
    missing = sorted(set(english_flat) - set(target_flat))
    extra = sorted(set(target_flat) - set(english_flat))
    if extra and not prune:
        raise ValueError(
            f"{locale} has browser keys that English no longer has: {', '.join(extra)}. "
            "If they were removed from en.json on purpose, run the sync with --prune "
            "(inv i18n-update --prune)."
        )
    for key in extra:
        _delete_json_path(target, key)
    for key in missing:
        _set_json_path(target, key, "")
    _write_json(target_path, target)
    return BrowserSync(added=missing, pruned=extra)


def _placeholders(value: str, pattern: re.Pattern[str]) -> set[str]:
    return set(pattern.findall(value))


def validate_browser_catalog(locale: str, require_translated: bool = False) -> list[str]:
    locale = _validate_locale(locale)
    english = flatten_json(_read_json(ENGLISH_BROWSER_PATH))
    translated = flatten_json(_read_json(BROWSER_LOCALES_DIR / f"{locale}.json"))
    errors = []
    missing = sorted(set(english) - set(translated))
    extra = sorted(set(translated) - set(english))
    if missing:
        errors.append(f"{locale}: missing keys: {', '.join(missing)}")
    if extra:
        errors.append(f"{locale}: unexpected keys: {', '.join(extra)}")
    for key in sorted(set(english) & set(translated)):
        expected = _placeholders(english[key], I18NEXT_PLACEHOLDER_RE)
        actual = _placeholders(translated[key], I18NEXT_PLACEHOLDER_RE)
        if expected != actual:
            errors.append(
                f"{locale}:{key}: placeholders differ; expected {sorted(expected)}, got {sorted(actual)}"
            )
        if require_translated and not translated[key].strip():
            errors.append(f"{locale}:{key}: translation is empty")
    return errors


def _catalog_path(locale: str) -> Path:
    return TRANSLATIONS_DIR / locale / "LC_MESSAGES" / "messages.po"


def _read_catalog(path: Path, locale: str | None = None) -> Catalog:
    with path.open("rb") as handle:
        return read_po(handle, locale=locale)


def validate_gettext_catalog(
    locale: str,
    require_translated: bool = False,
    *,
    show_progress: bool = False,
) -> list[str]:
    locale = _validate_locale(locale)
    source = _read_catalog(POT_PATH)
    translated = _read_catalog(_catalog_path(locale), locale)
    errors = []
    source_messages = {
        (message.context, message.id): message
        for message in source
        if message.id
    }
    translated_messages = {
        (message.context, message.id): message
        for message in translated
        if message.id
    }
    missing = source_messages.keys() - translated_messages.keys()
    extra = translated_messages.keys() - source_messages.keys()
    for context, message_id in sorted(missing, key=str):
        errors.append(f"{locale}: missing gettext message {context!r}:{message_id!r}")
    for context, message_id in sorted(extra, key=str):
        errors.append(f"{locale}: unexpected gettext message {context!r}:{message_id!r}")
    common_keys = sorted(source_messages.keys() & translated_messages.keys(), key=str)
    progress = tqdm(
        common_keys,
        desc=f"Validating gettext {locale}",
        unit="msg",
        leave=False,
    )
    for key in progress:
        source_message = source_messages[key]
        target_message = translated_messages[key]
        source_parts = source_message.id if isinstance(source_message.id, tuple) else (source_message.id,)
        target_parts = target_message.string if isinstance(target_message.string, tuple) else (target_message.string,)
        expected = set().union(*(_placeholders(part, GETTEXT_PLACEHOLDER_RE) for part in source_parts))
        actual = set().union(*(_placeholders(part or "", GETTEXT_PLACEHOLDER_RE) for part in target_parts))
        if expected != actual:
            errors.append(
                f"{locale}:{source_message.id!r}: placeholders differ; "
                f"expected {sorted(expected)}, got {sorted(actual)}"
            )
        if require_translated and not all((part or "").strip() for part in target_parts):
            errors.append(f"{locale}:{source_message.id!r}: translation is empty")
    return errors


def check_catalogs(require_translated: bool = False, *, show_progress: bool = False) -> None:
    errors = []
    for path in sorted(BROWSER_LOCALES_DIR.glob("*.json")):
        if path.stem == "en" or path.stem.endswith(".review"):
            continue
        errors.extend(validate_browser_catalog(path.stem, require_translated))
        po_path = _catalog_path(path.stem)
        if po_path.exists():
            errors.extend(validate_gettext_catalog(
                path.stem,
                require_translated,
                show_progress=show_progress,
            ))
    if errors:
        raise ValueError("\n".join(errors))


def _missing_browser_entries(locale: str) -> list[TranslationEntry]:
    english = flatten_json(_read_json(ENGLISH_BROWSER_PATH))
    target = flatten_json(_read_json(BROWSER_LOCALES_DIR / f"{locale}.json"))
    return [
        TranslationEntry(id=f"browser:{key}", source=english[key], context=key)
        for key in sorted(english)
        if not target.get(key, "").strip()
    ]


def _browser_entries(locale: str, *, overwrite: bool = False) -> list[TranslationEntry]:
    english = flatten_json(_read_json(ENGLISH_BROWSER_PATH))
    target = flatten_json(_read_json(BROWSER_LOCALES_DIR / f"{locale}.json"))
    return [
        TranslationEntry(id=f"browser:{key}", source=english[key], context=key)
        for key in sorted(english)
        if overwrite or not target.get(key, "").strip()
    ]


def _gettext_source_parts(message: Message, catalog: Catalog) -> str | list[str]:
    if not isinstance(message.id, tuple):
        return message.id
    singular, plural = message.id
    if catalog.num_plurals <= 1:
        return [singular]
    return [singular, plural, *([plural] * (catalog.num_plurals - 2))]


def _gettext_entries(locale: str, *, overwrite: bool = False) -> list[TranslationEntry]:
    catalog = _read_catalog(_catalog_path(locale), locale)
    entries = []
    for index, message in enumerate(message for message in catalog if message.id):
        strings = message.string if isinstance(message.string, tuple) else (message.string,)
        if not overwrite and all((string or "").strip() for string in strings):
            continue
        entries.append(
            TranslationEntry(
                id=f"gettext:{index}",
                source=_gettext_source_parts(message, catalog),
                context=message.context or "",
            )
        )
    return entries


def _missing_gettext_entries(locale: str) -> list[TranslationEntry]:
    return _gettext_entries(locale)


def _mask_protected_text(value: str, pattern: re.Pattern[str]) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}
    token_index = 0
    token_re = re.compile("|".join([
        pattern.pattern,
        HTML_TAG_RE.pattern,
        BRACE_PLACEHOLDER_RE.pattern,
    ]))

    def replace(match: re.Match[str]) -> str:
        nonlocal token_index
        token = f"YAFFOTOKEN{token_index}"
        protected[token] = match.group(0)
        token_index += 1
        return token

    return token_re.sub(replace, value), protected


def _unmask_protected_text(value: str, protected: dict[str, str]) -> str:
    restored = value
    for token, original in protected.items():
        restored = restored.replace(token, original)
    return restored


def _translate_text_with_deep_translator(value: str, translator, pattern: re.Pattern[str]) -> str:
    if not value.strip():
        return value
    parts = []
    cursor = 0
    for match in PROTECTED_TEXT_RE.finditer(value):
        if match.start() > cursor:
            parts.append(_translate_plain_text(value[cursor:match.start()], translator))
        parts.append(match.group(0))
        cursor = match.end()
    if cursor < len(value):
        parts.append(_translate_plain_text(value[cursor:], translator))
    return "".join(parts)


def _translate_plain_text(value: str, translator, retry_count =0) -> str:
    if not value.strip() or not re.search(r"[A-Za-z]", value):
        return value
    leading = value[:len(value) - len(value.lstrip())]
    trailing = value[len(value.rstrip()):]
    core = value.strip()
    try:
        return f"{leading}{translator.translate(core)}{trailing}"
    except TranslationNotFound:
        if retry_count > 2:
            logger.error(f"Could not translate {value!r}")
            return value
        return _translate_plain_text(value, translator, retry_count + 1)



class _ThrottledTranslator:
    """Serializes calls to a deep-translator backend under its request-rate limit."""

    def __init__(self, translator):
        self._translator = translator
        self._last_request = 0.0

    def translate(self, value: str) -> str:
        attempt = 0
        while True:
            wait = DEEP_TRANSLATOR_MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                return self._translator.translate(value)
            except TooManyRequests:
                if attempt >= DEEP_TRANSLATOR_MAX_RETRIES:
                    raise
                backoff = DEEP_TRANSLATOR_BACKOFF_SECONDS * 2 ** attempt
                logger.warning(f"Translation rate limited; retrying in {backoff:.0f}s")
                time.sleep(backoff)
                attempt += 1


def _deep_translator(locale: str):
    from deep_translator import GoogleTranslator

    target = DEEP_TRANSLATOR_TARGETS.get(locale, locale)
    return _ThrottledTranslator(GoogleTranslator(source="en", target=target))


def _translate_batch_with_deep_translator(
    entries: list[TranslationEntry], locale: str
) -> dict[str, str | list[str]]:
    translated: dict[str, str | list[str]] = {}
    translator = _deep_translator(locale)
    for entry in entries:
        pattern = GETTEXT_PLACEHOLDER_RE if entry.id.startswith("gettext:") else I18NEXT_PLACEHOLDER_RE
        if isinstance(entry.source, list):
            translated[entry.id] = [
                _translate_text_with_deep_translator(part, translator, pattern)
                for part in entry.source
            ]
        else:
            translated[entry.id] = _translate_text_with_deep_translator(entry.source, translator, pattern)
    return translated


def _translate_batch(entries: list[TranslationEntry], locale: str) -> dict[str, str | list[str]]:
    return _translate_batch_with_engine(entries, locale, engine="deep-translator")


def _translate_batch_with_engine(
    entries: list[TranslationEntry],
    locale: str,
    *,
    engine: str,
    llm: CliOptions = CliOptions(),
) -> dict[str, str | list[str]]:
    if engine == "deep-translator":
        return _translate_batch_with_deep_translator(entries, locale)
    if engine not in {"llm", "auto"}:
        raise ValueError(f"Unknown translation engine: {engine}")
    try:
        return translate_with_cli([asdict(entry) for entry in entries], locale, llm)
    except CliUnavailableError:
        if engine == "auto" and llm.cli == "auto":
            return _translate_batch_with_deep_translator(entries, locale)
        raise


def _validate_translation(entry: TranslationEntry, translated: str | list[str]) -> None:
    if isinstance(entry.source, list) != isinstance(translated, list):
        raise ValueError(f"{entry.id}: plural/value shape changed")
    source_parts = entry.source if isinstance(entry.source, list) else [entry.source]
    target_parts = translated if isinstance(translated, list) else [translated]
    if len(source_parts) != len(target_parts):
        raise ValueError(f"{entry.id}: plural/value shape changed")
    pattern = GETTEXT_PLACEHOLDER_RE if entry.id.startswith("gettext:") else I18NEXT_PLACEHOLDER_RE
    expected = set().union(*(_placeholders(part, pattern) for part in source_parts))
    actual = set().union(*(_placeholders(part, pattern) for part in target_parts))
    if expected != actual:
        raise ValueError(
            f"{entry.id}: placeholders differ; expected {sorted(expected)}, got {sorted(actual)}"
        )


def translate_missing(
    locale: str,
    *,
    dry_run: bool = False,
    keys_only: bool = False,
    overwrite: bool = False,
    batch_size: int = 20,
    engine: str = "deep-translator",
    llm_cli: str = "auto",
    llm_model: str | None = None,
    llm_timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[str]:
    locale = _validate_locale(locale)
    if batch_size <= 0 or llm_timeout <= 0:
        raise ValueError("Batch size and LLM CLI timeout must be greater than zero.")
    sync_browser_locale(locale)
    entries = _missing_browser_entries(locale) + _missing_gettext_entries(locale)
    if overwrite:
        entries = _browser_entries(locale, overwrite=True) + _gettext_entries(locale, overwrite=True)
    if keys_only or not entries:
        return [entry.id for entry in entries]

    translated_values: dict[str, str | list[str]] = {}
    batches = [entries[i: i + batch_size] for i in range(0, len(entries), batch_size)]
    logger.info(f"Translating {len(batches)} batches for {locale}")
    skipped: list[str] = []
    for batch_index, batch in enumerate(batches, start=1):
        logger.info(f"Translating batch {batch_index}/{len(batches)} for {locale} with {engine}")
        pending = batch
        for attempt in range(TRANSLATION_MAX_ATTEMPTS):
            batch_values = _translate_batch_with_engine(
                pending, locale, engine=engine, llm=CliOptions(llm_cli, llm_model, llm_timeout))
            rejected = []
            for entry in pending:
                try:
                    if entry.id not in batch_values:
                        raise ValueError(f"{entry.id}: response omitted this entry")
                    _validate_translation(entry, batch_values[entry.id])
                except ValueError as error:
                    logger.warning(f"Rejected translation (attempt {attempt + 1}): {error}")
                    rejected.append(entry)
                    continue
                translated_values[entry.id] = batch_values[entry.id]
            pending = rejected
            if not pending:
                break
        skipped.extend(entry.id for entry in pending)
        logger.info(f"Translated {len(batch) - len(pending)} entries")
    if skipped:
        logger.warning(
            f"Left {len(skipped)} {locale} entries untranslated; rerun to retry: {', '.join(skipped)}"
        )
    entries = [entry for entry in entries if entry.id in translated_values]
    if dry_run:
        return [f"{entry.id} = {translated_values[entry.id]}" for entry in entries]

    browser = _read_json(BROWSER_LOCALES_DIR / f"{locale}.json")
    has_gettext_entries = any(entry.id.startswith("gettext:") for entry in entries)
    catalog = _read_catalog(_catalog_path(locale), locale) if has_gettext_entries else None
    gettext_messages: list[Message] = (
        [message for message in catalog if message.id] if catalog is not None else []
    )
    for entry in entries:
        translated = translated_values[entry.id]
        if entry.id.startswith("browser:"):
            key = entry.id.removeprefix("browser:")
            _set_json_path(browser, key, str(translated))
        else:
            index = int(entry.id.removeprefix("gettext:"))
            message = gettext_messages[index]
            message.string = tuple(translated) if isinstance(translated, list) else translated
            message.flags.discard("fuzzy")
    _write_json(BROWSER_LOCALES_DIR / f"{locale}.json", browser)
    if catalog is not None:
        with _catalog_path(locale).open("wb") as handle:
            write_po(handle, catalog, width=100)
    return [entry.id for entry in entries]


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--locale", required=True)
    sync_parser.add_argument("--prune", action="store_true",
                             help="remove keys that en.json no longer has")
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--require-translated", action="store_true")
    translate_parser = subparsers.add_parser("translate")
    translate_parser.add_argument("--locale", required=True)
    translate_parser.add_argument("--dry-run", action="store_true")
    translate_parser.add_argument("--keys-only", action="store_true")
    translate_parser.add_argument("--overwrite", action="store_true")
    translate_parser.add_argument("--batch-size", type=int, default=20)
    translate_parser.add_argument(
        "--engine",
        choices=("auto", "llm", "deep-translator"),
        default="auto",
    )
    translate_parser.add_argument("--llm-cli", choices=("auto", "codex", "claude"), default="auto",
                                  help="LLM CLI to use; auto prefers Codex, then Claude Code")
    translate_parser.add_argument("--llm-model", help="Override the CLI's configured model")
    translate_parser.add_argument("--llm-timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
                                  help="Maximum seconds per LLM batch")
    args = parser.parse_args()

    if args.command == "sync":
        result = sync_browser_locale(args.locale, prune=args.prune)
        lines = [f"+ {key}" for key in result.added] + [f"- {key}" for key in result.pruned]
        print("\n".join(lines) if lines else "Browser catalog is up to date")
    elif args.command == "check":
        check_catalogs(args.require_translated, show_progress=True)
        print("Catalogs are valid")
    else:
        result = translate_missing(
            args.locale,
            dry_run=args.dry_run,
            keys_only=args.keys_only,
            overwrite=args.overwrite,
            batch_size=args.batch_size,
            engine=args.engine,
            llm_cli=args.llm_cli,
            llm_model=args.llm_model,
            llm_timeout=args.llm_timeout,
        )
        print("\n".join(result) if result else "No missing translations")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from babel.messages.catalog import Catalog, Message
from babel.messages.pofile import read_po, write_po

from yaffo.common import BUNDLE_ROOT

ROOT = BUNDLE_ROOT
POT_PATH = ROOT / "messages.pot"
TRANSLATIONS_DIR = ROOT / "yaffo" / "translations"
BROWSER_LOCALES_DIR = ROOT / "yaffo" / "static" / "locales"
ENGLISH_BROWSER_PATH = BROWSER_LOCALES_DIR / "en.json"
LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
I18NEXT_PLACEHOLDER_RE = re.compile(r"{{\s*([\w.-]+)\s*}}")
GETTEXT_PLACEHOLDER_RE = re.compile(r"%\(([\w.-]+)\)[#0 +\-]?\d*(?:\.\d+)?[a-zA-Z]")


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


def sync_browser_locale(locale: str) -> list[str]:
    locale = _validate_locale(locale)
    english = _read_json(ENGLISH_BROWSER_PATH)
    english_flat = flatten_json(english)
    target_path = BROWSER_LOCALES_DIR / f"{locale}.json"
    target = _read_json(target_path) if target_path.exists() else {}
    target_flat = flatten_json(target)
    missing = sorted(set(english_flat) - set(target_flat))
    extra = sorted(set(target_flat) - set(english_flat))
    if extra:
        raise ValueError(f"{locale} has unexpected browser keys: {', '.join(extra)}")
    for key in missing:
        _set_json_path(target, key, "")
    _write_json(target_path, target)
    return missing


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


def validate_gettext_catalog(locale: str, require_translated: bool = False) -> list[str]:
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
    for key in source_messages.keys() & translated_messages.keys():
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


def check_catalogs(require_translated: bool = False) -> None:
    errors = []
    for path in sorted(BROWSER_LOCALES_DIR.glob("*.json")):
        if path.stem == "en" or path.stem.endswith(".review"):
            continue
        errors.extend(validate_browser_catalog(path.stem, require_translated))
        po_path = _catalog_path(path.stem)
        if po_path.exists():
            errors.extend(validate_gettext_catalog(path.stem, require_translated))
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


def _missing_gettext_entries(locale: str) -> list[TranslationEntry]:
    catalog = _read_catalog(_catalog_path(locale), locale)
    entries = []
    for index, message in enumerate(message for message in catalog if message.id):
        strings = message.string if isinstance(message.string, tuple) else (message.string,)
        if all((string or "").strip() for string in strings):
            continue
        source = list(message.id) if isinstance(message.id, tuple) else message.id
        entries.append(
            TranslationEntry(
                id=f"gettext:{index}",
                source=source,
                context=message.context or "",
            )
        )
    return entries


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.DOTALL)
    return json.loads(stripped)


def _translate_batch(entries: list[TranslationEntry], locale: str) -> dict[str, str | list[str]]:
    create_app = importlib.import_module("yaffo.app").create_app
    llm_config = importlib.import_module("yaffo.site_agents.llm_config")
    create_model_client = importlib.import_module(
        "yaffo.site_agents.model_clients.factory"
    ).create_model_client
    app = create_app()
    with app.app_context():
        model = llm_config.get_model()
        api_key = llm_config.get_api_key_for_selected_model()
        if not api_key:
            raise ValueError("No API key configured for the selected model")
        client = create_model_client(
            model=model,
            system_prompt=(
                "You translate application UI text. Return only valid JSON with this shape: "
                '{"translations":[{"id":"unchanged id","value":"translation or array"}]}. '
                "Preserve every placeholder, HTML tag, and array shape exactly. "
                "Do not translate product names, identifiers, or placeholders."
            ),
            providers=[],
            api_key=api_key,
            max_tokens=8000,
        )
        client.add_user_message(json.dumps({
            "target_locale": locale,
            "entries": [
                {"id": entry.id, "source": entry.source, "context": entry.context}
                for entry in entries
            ],
        }, ensure_ascii=False))
        response = client.call_model_api()
        if response is None or not response.text:
            raise ValueError("The selected model returned no translation")
    payload = _extract_json(response.text)
    return {item["id"]: item["value"] for item in payload["translations"]}


def _validate_translation(entry: TranslationEntry, translated: str | list[str]) -> None:
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
) -> list[str]:
    locale = _validate_locale(locale)
    sync_browser_locale(locale)
    entries = _missing_browser_entries(locale) + _missing_gettext_entries(locale)
    if overwrite:
        raise ValueError("--overwrite is reserved for a future reviewed-translation workflow")
    if keys_only or not entries:
        return [entry.id for entry in entries]

    translated_values: dict[str, str | list[str]] = {}
    for start in range(0, len(entries), batch_size):
        batch = entries[start:start + batch_size]
        batch_values = _translate_batch(batch, locale)
        for entry in batch:
            if entry.id not in batch_values:
                raise ValueError(f"Model response omitted {entry.id}")
            _validate_translation(entry, batch_values[entry.id])
            translated_values[entry.id] = batch_values[entry.id]
    if dry_run:
        return [f"{entry.id} = {translated_values[entry.id]}" for entry in entries]

    browser = _read_json(BROWSER_LOCALES_DIR / f"{locale}.json")
    has_gettext_entries = any(entry.id.startswith("gettext:") for entry in entries)
    catalog = _read_catalog(_catalog_path(locale), locale) if has_gettext_entries else None
    gettext_messages: list[Message] = (
        [message for message in catalog if message.id] if catalog is not None else []
    )
    review_keys = []
    for entry in entries:
        translated = translated_values[entry.id]
        if entry.id.startswith("browser:"):
            key = entry.id.removeprefix("browser:")
            _set_json_path(browser, key, str(translated))
            review_keys.append(key)
        else:
            index = int(entry.id.removeprefix("gettext:"))
            message = gettext_messages[index]
            message.string = tuple(translated) if isinstance(translated, list) else translated
            message.flags.add("fuzzy")
    _write_json(BROWSER_LOCALES_DIR / f"{locale}.json", browser)
    _write_json(BROWSER_LOCALES_DIR / f"{locale}.review.json", {"generated": review_keys})
    if catalog is not None:
        with _catalog_path(locale).open("wb") as handle:
            write_po(handle, catalog, width=100)
    return [entry.id for entry in entries]


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--locale", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--require-translated", action="store_true")
    translate_parser = subparsers.add_parser("translate")
    translate_parser.add_argument("--locale", required=True)
    translate_parser.add_argument("--dry-run", action="store_true")
    translate_parser.add_argument("--keys-only", action="store_true")
    translate_parser.add_argument("--overwrite", action="store_true")
    translate_parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()

    if args.command == "sync":
        missing = sync_browser_locale(args.locale)
        print("\n".join(missing) if missing else "Browser catalog is up to date")
    elif args.command == "check":
        check_catalogs(args.require_translated)
        print("Catalogs are valid")
    else:
        result = translate_missing(
            args.locale,
            dry_run=args.dry_run,
            keys_only=args.keys_only,
            overwrite=args.overwrite,
            batch_size=args.batch_size,
        )
        print("\n".join(result) if result else "No missing translations")


if __name__ == "__main__":
    main()

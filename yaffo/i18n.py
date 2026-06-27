from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import has_request_context, request
from flask_babel import Babel
from sqlalchemy.exc import OperationalError

from yaffo.common import BUNDLE_ROOT
from yaffo.db import db
from yaffo.db.models import ApplicationSettings

DEFAULT_LOCALE = "en"
LOCALE_SETTING = "locale"
SUPPORTED_LOCALES = {
    "en": "English",
    "de": "Deutsch",
    "zh": "中文",
    "hi": "हिन्दी",
    "es": "Español",
    "ar": "العربية",
    "fr": "Français",
}
TRANSLATIONS_DIR = BUNDLE_ROOT / "yaffo" / "translations"

babel = Babel()


@dataclass(frozen=True)
class LocaleOption:
    code: str
    label: str


def supported_locale_options() -> list[LocaleOption]:
    return [LocaleOption(code=code, label=label) for code, label in SUPPORTED_LOCALES.items()]


def normalize_locale(locale: str | None) -> str | None:
    if not locale:
        return None
    normalized = locale.replace("_", "-").split("-", 1)[0].lower()
    return normalized if normalized in SUPPORTED_LOCALES else None


def get_saved_locale(session=None) -> str | None:
    session = session or db.session
    try:
        row = session.query(ApplicationSettings).filter_by(name=LOCALE_SETTING).first()
    except OperationalError:
        return None
    return normalize_locale(row.value) if row else None


def select_locale() -> str:
    saved = get_saved_locale()
    if saved:
        return saved
    # A lazy_gettext string (e.g. a built-in theme/automation label) can resolve in an
    # app context with no request — Accept-Language only applies when a request exists.
    if has_request_context():
        accepted = request.accept_languages.best_match(list(SUPPORTED_LOCALES))
        return normalize_locale(accepted) or DEFAULT_LOCALE
    return DEFAULT_LOCALE


def set_locale(locale: str) -> bool:
    normalized = normalize_locale(locale)
    if normalized is None:
        return False
    row = db.session.query(ApplicationSettings).filter_by(name=LOCALE_SETTING).first()
    if row is None:
        db.session.add(ApplicationSettings(name=LOCALE_SETTING, type="string", value=normalized))
    else:
        row.value = normalized
    db.session.commit()
    return True


def text_direction(locale: str) -> str:
    return "rtl" if locale.split("-", 1)[0] in {"ar", "fa", "he", "ur"} else "ltr"


def init_i18n(app) -> None:
    app.config.setdefault("BABEL_DEFAULT_LOCALE", DEFAULT_LOCALE)
    app.config.setdefault("BABEL_TRANSLATION_DIRECTORIES", str(TRANSLATIONS_DIR))
    babel.init_app(app, locale_selector=select_locale)

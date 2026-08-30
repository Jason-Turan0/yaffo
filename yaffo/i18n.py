from __future__ import annotations

from dataclasses import dataclass

from flask import has_request_context, request
from flask_babel import Babel, Domain, get_domain, get_locale
from sqlalchemy.exc import OperationalError

from yaffo.common import BUNDLE_ROOT
from yaffo.db import db
from yaffo.db.models import ApplicationSettings
from yaffo.logging_config import get_logger

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
logger = get_logger(__name__, "webapp")


@dataclass(frozen=True)
class LocaleOption:
    code: str
    label: str


class LoggingDomain(Domain):
    """Flask-Babel domain that warns when a non-default locale misses a key."""

    def __init__(self, *args, default_locale: str = DEFAULT_LOCALE, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_locale = default_locale
        self._logged_missing_keys: set[tuple[str, str]] = set()

    def _missing(self, key: str, *, plural: bool = False) -> bool:
        locale = str(get_locale() or self.default_locale)
        if normalize_locale(locale) == normalize_locale(self.default_locale):
            return False
        translations = self.get_translations()
        catalog = getattr(translations, "_catalog", {})
        if key in catalog:
            return False
        if plural and (key, 0) in catalog:
            return False
        return True

    def _warn_missing(self, key: str) -> None:
        locale = str(get_locale() or self.default_locale)
        cache_key = (locale, key)
        if cache_key in self._logged_missing_keys:
            return
        self._logged_missing_keys.add(cache_key)
        logger.warning("Missing translation for locale=%s key=%r", locale, key)

    def _catalog_key(self, string: str, context: str | None = None) -> str:
        return f"{context}\x04{string}" if context else string

    def gettext(self, string, **variables):
        key = self._catalog_key(string)
        if self._missing(key):
            self._warn_missing(key)
        return super().gettext(string, **variables)

    def gettext_unformatted(self, string):
        key = self._catalog_key(string)
        if self._missing(key):
            self._warn_missing(key)
        return self.get_translations().ugettext(string)

    def ngettext(self, singular, plural, num, **variables):
        key = self._catalog_key(singular)
        if self._missing(key, plural=True):
            self._warn_missing(key)
        return super().ngettext(singular, plural, num, **variables)

    def ngettext_unformatted(self, singular, plural, num):
        key = self._catalog_key(singular)
        if self._missing(key, plural=True):
            self._warn_missing(key)
        return self.get_translations().ungettext(singular, plural, num)

    def pgettext(self, context, string, **variables):
        key = self._catalog_key(string, context)
        if self._missing(key):
            self._warn_missing(key)
        return super().pgettext(context, string, **variables)

    def pgettext_unformatted(self, context, string):
        key = self._catalog_key(string, context)
        if self._missing(key):
            self._warn_missing(key)
        return self.get_translations().upgettext(context, string)

    def npgettext(self, context, singular, plural, num, **variables):
        key = self._catalog_key(singular, context)
        if self._missing(key, plural=True):
            self._warn_missing(key)
        return super().npgettext(context, singular, plural, num, **variables)

    def npgettext_unformatted(self, context, singular, plural, num):
        key = self._catalog_key(singular, context)
        if self._missing(key, plural=True):
            self._warn_missing(key)
        return self.get_translations().unpgettext(context, singular, plural, num)


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
    babel_config = app.extensions["babel"]
    babel.__dict__["domain_instance"] = LoggingDomain(
        translation_directories=babel_config.translation_directories,
        domain=babel_config.default_domain,
        default_locale=DEFAULT_LOCALE,
    )
    app.jinja_env.install_gettext_callables(
        gettext=lambda s: get_domain().gettext_unformatted(s),
        ngettext=lambda s, p, n: get_domain().ngettext_unformatted(s, p, n),
        newstyle=True,
        pgettext=lambda c, s: get_domain().pgettext_unformatted(c, s),
        npgettext=lambda c, s, p, n: get_domain().npgettext_unformatted(c, s, p, n),
    )

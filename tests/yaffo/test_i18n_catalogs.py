import json
from pathlib import Path

import pytest

from yaffo.i18n import normalize_locale, text_direction
import scripts.i18n_catalogs as i18n_catalogs
from scripts.i18n_catalogs import (
    BROWSER_LOCALES_DIR,
    I18NEXT_PLACEHOLDER_RE,
    POT_PATH,
    TranslationEntry,
    flatten_json,
    validate_browser_catalog,
    validate_gettext_catalog,
)

pytestmark = pytest.mark.unit


def test_normalize_locale_accepts_supported_language_variants():
    assert normalize_locale("de-DE") == "de"
    assert normalize_locale("en_US") == "en"
    assert normalize_locale("zh-CN") == "zh"
    assert normalize_locale("hi-IN") == "hi"
    assert normalize_locale("es-MX") == "es"
    assert normalize_locale("ar-SA") == "ar"
    assert normalize_locale("fr-CA") == "fr"
    assert normalize_locale("pt-BR") is None


def test_text_direction_supports_rtl_languages():
    assert text_direction("en") == "ltr"
    assert text_direction("ar") == "rtl"


def test_browser_catalogs_have_identical_keys_and_placeholders():
    english = flatten_json(json.loads((BROWSER_LOCALES_DIR / "en.json").read_text(encoding="utf-8")))
    for path in sorted(BROWSER_LOCALES_DIR.glob("*.json")):
        if path.stem == "en" or path.stem.endswith(".review"):
            continue
        translated = flatten_json(json.loads(path.read_text(encoding="utf-8")))
        assert translated.keys() == english.keys(), path.stem
        for key, source in english.items():
            assert set(I18NEXT_PLACEHOLDER_RE.findall(translated[key])) == set(
                I18NEXT_PLACEHOLDER_RE.findall(source)
            ), f"{path.stem}:{key}"
        assert validate_browser_catalog(path.stem, require_translated=True) == []


def test_gettext_catalogs_have_identical_messages_and_placeholders():
    for locale in ("de", "zh", "hi", "es", "ar", "fr"):
        assert validate_gettext_catalog(locale, require_translated=True) == []


def test_no_missing_translations_against_english_baselines():
    english_browser = flatten_json(json.loads((BROWSER_LOCALES_DIR / "en.json").read_text(encoding="utf-8")))
    english_gettext = {
        (message.context, message.id): message
        for message in i18n_catalogs._read_catalog(POT_PATH)
        if message.id
    }
    failures = []

    for path in sorted(BROWSER_LOCALES_DIR.glob("*.json")):
        if path.stem == "en" or path.stem.endswith(".review"):
            continue

        translated_browser = flatten_json(json.loads(path.read_text(encoding="utf-8")))
        missing_browser = sorted(set(english_browser) - set(translated_browser))
        empty_browser = sorted(
            key
            for key in set(english_browser) & set(translated_browser)
            if not translated_browser[key].strip()
        )
        if missing_browser:
            failures.append(f"{path.stem}: missing JavaScript keys: {', '.join(missing_browser)}")
        if empty_browser:
            failures.append(f"{path.stem}: empty JavaScript translations: {', '.join(empty_browser)}")

        translated_gettext = {
            (message.context, message.id): message
            for message in i18n_catalogs._read_catalog(i18n_catalogs._catalog_path(path.stem), path.stem)
            if message.id
        }
        missing_gettext = sorted(set(english_gettext) - set(translated_gettext), key=str)
        empty_gettext = []
        for key in sorted(set(english_gettext) & set(translated_gettext), key=str):
            translated = translated_gettext[key]
            parts = translated.string if isinstance(translated.string, tuple) else (translated.string,)
            if not all((part or "").strip() for part in parts):
                empty_gettext.append(str(key[1]))
        if missing_gettext:
            failures.append(
                f"{path.stem}: missing Babel messages: "
                + ", ".join(str(message_id) for _, message_id in missing_gettext)
            )
        if empty_gettext:
            failures.append(f"{path.stem}: empty Babel translations: {', '.join(empty_gettext)}")

    assert failures == []


def test_i18next_vendor_asset_is_packaged():
    asset = Path("yaffo/static/vendor/i18next/25.7.4/i18next.min.js")
    assert asset.is_file()
    assert "i18next" in asset.read_text(encoding="utf-8")[:500]


def test_translate_missing_populates_browser_keys(
    monkeypatch,
    tmp_path,
):
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    english_path = locales_dir / "en.json"
    english_path.write_text(
        json.dumps({"common": {"greeting": "Hello {{name}}", "save": "Save"}}),
        encoding="utf-8",
    )
    (locales_dir / "de.json").write_text(
        json.dumps({"common": {"save": "Speichern"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(i18n_catalogs, "BROWSER_LOCALES_DIR", locales_dir)
    monkeypatch.setattr(i18n_catalogs, "ENGLISH_BROWSER_PATH", english_path)
    monkeypatch.setattr(i18n_catalogs, "_missing_gettext_entries", lambda locale: [])
    monkeypatch.setattr(
        i18n_catalogs,
        "_translate_batch_with_engine",
        lambda entries, locale, engine: {
            "browser:common.greeting": "Hallo {{name}}",
        },
    )

    translated = i18n_catalogs.translate_missing("de")

    assert translated == ["browser:common.greeting"]
    catalog = json.loads((locales_dir / "de.json").read_text(encoding="utf-8"))
    assert catalog == {
        "common": {
            "greeting": "Hallo {{name}}",
            "save": "Speichern",
        }
    }
    assert not (locales_dir / "de.review.json").exists()


def test_translate_missing_overwrite_regenerates_existing_browser_entries(
    monkeypatch,
    tmp_path,
):
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    english_path = locales_dir / "en.json"
    english_path.write_text(
        json.dumps({"common": {"greeting": "Hello {{name}}", "save": "Save"}}),
        encoding="utf-8",
    )
    (locales_dir / "es.json").write_text(
        json.dumps({"common": {"greeting": "Hello {{name}}", "save": "Save"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(i18n_catalogs, "BROWSER_LOCALES_DIR", locales_dir)
    monkeypatch.setattr(i18n_catalogs, "ENGLISH_BROWSER_PATH", english_path)
    monkeypatch.setattr(i18n_catalogs, "_gettext_entries", lambda locale, overwrite=False: [])
    monkeypatch.setattr(
        i18n_catalogs,
        "_translate_batch_with_engine",
        lambda entries, locale, engine: {
            "browser:common.greeting": "Hola {{name}}",
            "browser:common.save": "Guardar",
        },
    )

    translated = i18n_catalogs.translate_missing("es", overwrite=True)

    assert translated == ["browser:common.greeting", "browser:common.save"]
    catalog = json.loads((locales_dir / "es.json").read_text(encoding="utf-8"))
    assert catalog == {"common": {"greeting": "Hola {{name}}", "save": "Guardar"}}


def test_generated_translation_must_preserve_placeholders():
    entry = TranslationEntry(
        id="browser:common.greeting",
        source="Hello {{name}}",
        context="common.greeting",
    )

    with pytest.raises(ValueError, match="placeholders differ"):
        i18n_catalogs._validate_translation(entry, "Hallo")


def test_deep_translator_backend_preserves_placeholders_and_html(monkeypatch):
    class FakeTranslator:
        def translate(self, value):
            return value.replace("Delete", "Eliminar").replace("from", "de")

    monkeypatch.setattr(i18n_catalogs, "_deep_translator", lambda locale: FakeTranslator())

    translated = i18n_catalogs._translate_batch_with_deep_translator(
        [
            TranslationEntry(
                id="browser:people.delete.message",
                source='Delete <strong>{{name}}</strong> from {totalCount}',
                context="people.delete.message",
            )
        ],
        "es",
    )

    assert translated == {
        "browser:people.delete.message": 'Eliminar <strong>{{name}}</strong> de {totalCount}'
    }


def test_i18next_bootstrap_treats_top_level_catalog_objects_as_namespaces():
    source = Path("yaffo/static/i18n.js").read_text(encoding="utf-8")
    assert "[locale]: catalog" in source
    assert "defaultNS: 'common'" in source
    assert "escapeValue: false" in source


def test_shared_javascript_components_use_catalog_keys():
    component_sources = [
        Path("yaffo/static/components/chat_dialog.js"),
        Path("yaffo/static/components/confirm-dialog.js"),
        Path("yaffo/static/components/cron_builder.js"),
        Path("yaffo/static/components/folder_picker.js"),
        Path("yaffo/static/multi-select.js"),
        Path("yaffo/static/searchable-select.js"),
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in component_sources)

    assert "components:chat.startFailed" in source
    assert "components:folderPicker.selectFolder" in source
    assert "components:select.noResults" in source
    assert "components:cron." in source


def test_trigger_editor_waits_for_localized_cron_builder():
    builder_source = Path("yaffo/static/components/cron_builder.js").read_text(encoding="utf-8")
    automation_source = Path("yaffo/static/utilities/automations.js").read_text(encoding="utf-8")

    assert "COMPONENTS.cronBuilderReady =" in builder_source
    assert "return api;" in builder_source
    assert "await cronBuilderReady" in automation_source


def test_media_javascript_uses_catalog_keys_and_receives_i18n_service():
    media_sources = [
        Path("yaffo/static/filters/filter_config.js"),
        Path("yaffo/static/filters/location-autocomplete.js"),
        Path("yaffo/static/filters/tags.js"),
        Path("yaffo/static/media/face-reassign.js"),
        Path("yaffo/static/media/favorite.js"),
        Path("yaffo/static/media/gallery_video.js"),
        Path("yaffo/static/media/tags.js"),
        Path("yaffo/static/media/view.js"),
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in media_sources)
    gallery_template = Path("yaffo/templates/index.html").read_text(encoding="utf-8")
    detail_template = Path("yaffo/templates/media/view.html").read_text(encoding="utf-8")

    assert "media:favorite.updateFailed" in source
    assert "media:gallery.videoPlaybackFailed" in source
    assert "media:tags.updateSucceeded" in source
    assert "media:faces.reassign" in source
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in gallery_template
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in detail_template


def test_faces_javascript_uses_catalog_keys_and_receives_i18n_service():
    source = Path("yaffo/static/faces/index.js").read_text(encoding="utf-8")
    template = Path("yaffo/templates/faces/index.html").read_text(encoding="utf-8")
    babel_config = Path("babel.cfg").read_text(encoding="utf-8")

    assert "faces:assignment.noneSelected" in source
    assert "faces:assignment.clusterSkipped" in source
    assert "faces:people.nameRequired" in source
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in template
    assert "ngettext:1,2" in babel_config


def test_people_list_uses_catalog_keys_and_delegated_actions():
    source = Path("yaffo/static/people/list.js").read_text(encoding="utf-8")
    select_source = Path("yaffo/static/searchable-select.js").read_text(encoding="utf-8")
    template = Path("yaffo/templates/people/list.html").read_text(encoding="utf-8")

    assert "people:delete.message" in source
    assert "window.PHOTO_ORGANIZER.confirmDialog" in source
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in template
    assert 'data-action="edit"' in template
    assert 'data-action="delete"' in template
    assert "onclick=" not in template
    assert "this.select.addEventListener('change'" in select_source


def test_person_faces_uses_catalog_keys_and_page_initializer():
    source = Path("yaffo/static/people/faces.js").read_text(encoding="utf-8")
    template = Path("yaffo/templates/people/faces.html").read_text(encoding="utf-8")

    assert "window.PHOTO_ORGANIZER.initPersonFaces" in source
    assert "people:faces.selectRequired" in source
    assert "people:faces.removeMessage" in source
    assert "window.PHOTO_ORGANIZER.confirmDialog" in source
    assert "alert(" not in source
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in template
    assert "onclick=" not in template


def test_locations_map_uses_catalog_keys_and_application_config():
    source = Path("yaffo/static/locations/list.js").read_text(encoding="utf-8")
    template = Path("yaffo/templates/locations/list.html").read_text(encoding="utf-8")

    assert "locations:selection.massAssignment" in source
    assert "locations:update.succeeded" in source
    assert "locations:unknownLocation" in source
    assert "config.urls.locations_bulk_update" in source
    assert "config.urls.reverse_geocode_route" in source
    assert "window.APP_CONFIG.buildUrl" not in source
    assert "fetch('/locations/" not in source
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in template


def test_settings_media_controls_use_catalog_keys_and_delegated_actions():
    source = Path("yaffo/static/settings/index.js").read_text(encoding="utf-8")
    template = Path("yaffo/templates/settings/index.html").read_text(encoding="utf-8")

    assert "settings:media.addSucceeded" in source
    assert "settings:media.removeMessage" in source
    assert "settings:thumbnail.moveMessage" in source
    assert "settings:thumbnail.moveSucceeded" in source
    assert "config.urls.add_media_dir" in source
    assert "config.urls.update_thumbnail_dir" in source
    assert "window.location.reload()" in source
    assert "escapeHtml(dir.path)" in source
    assert 'data-action="remove-media-dir"' in template
    assert "onclick=" not in template
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in template


def test_settings_label_management_uses_gettext_and_delegated_filtering():
    source = Path("yaffo/static/settings/labels.js").read_text(encoding="utf-8")
    template = Path("yaffo/templates/settings/_labels.html").read_text(encoding="utf-8")
    routes = Path("yaffo/routes/settings.py").read_text(encoding="utf-8")

    assert '{{ _("Photo labels") }}' in template
    assert "_('Filter labels…')" in template
    assert '{{ _("Re-classify all photos") }}' in template
    assert "document.addEventListener('input'" in source
    assert "ngettext(" in routes
    assert "photo(s)" not in routes


def test_settings_llm_forms_use_gettext_and_localized_notifications():
    llm_template = Path("yaffo/templates/settings/_llm.html").read_text(encoding="utf-8")
    key_template = Path("yaffo/templates/settings/_llm_api_key.html").read_text(encoding="utf-8")
    routes = Path("yaffo/routes/settings.py").read_text(encoding="utf-8")

    assert '{{ _("AI Generation") }}' in llm_template
    assert '{{ _("Model") }}' in llm_template
    assert '_("%(provider)s API key:"' in key_template
    assert "_('API key')" in key_template
    assert 'gettext("AI model updated.")' in routes
    assert '"claude-sonnet-4-6": gettext(' in routes


def test_themes_page_uses_gettext_and_localized_javascript():
    source = Path("yaffo/static/themes_page/index.js").read_text(encoding="utf-8")
    template = Path("yaffo/templates/themes_page/index.html").read_text(encoding="utf-8")
    routes = Path("yaffo/routes/themes_page.py").read_text(encoding="utf-8")

    assert '{{ _("Themes - Yaffo") }}' in template
    assert '{{ _("New theme") }}' in template
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in template
    assert "themes:delete.message" in source
    assert "themes:chat.cancelMessage" in source
    assert "gettext(\"Theme name is required\")" in routes
    assert "RouteError" in routes


def test_custom_page_grid_uses_gettext_and_localized_javascript():
    detail_source = Path("yaffo/static/pages/detail.js").read_text(encoding="utf-8")
    grid_source = Path("yaffo/static/pages/grid.js").read_text(encoding="utf-8")
    presentation_template = Path("yaffo/templates/pages/presentation.html").read_text(encoding="utf-8")
    design_template = Path("yaffo/templates/pages/design.html").read_text(encoding="utf-8")
    widget_template = Path("yaffo/templates/pages/_widget.html").read_text(encoding="utf-8")
    routes = Path("yaffo/routes/pages.py").read_text(encoding="utf-8")

    assert "{{ _('Widget title') }}" in widget_template
    assert "_('%(title)s preview'" in widget_template
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in presentation_template
    assert "window.PHOTO_ORGANIZER.i18nReady.then((i18n)" in design_template
    assert "pages:delete.message" in detail_source
    assert "pages:widgets.deleteMessage" in grid_source
    assert "pages:chat.cancelMessage" in grid_source
    assert 'gettext("Untitled Page")' in routes


def test_page_designer_uses_gettext_localized_javascript_and_named_payloads():
    detail_source = Path("yaffo/static/pages/detail.js").read_text(encoding="utf-8")
    design_template = Path("yaffo/templates/pages/design.html").read_text(encoding="utf-8")
    routes = Path("yaffo/routes/pages.py").read_text(encoding="utf-8")

    assert '{{ _("Title") }}' in design_template
    assert "{{ _('Untitled Page') }}" in design_template
    assert '{{ _("Add widget") }}' in design_template
    assert "_('Assistant')" in design_template
    assert "onclick=" not in design_template
    assert "delete-page-button" in detail_source
    assert 'gettext("Message is required.")' in routes
    assert 'gettext("A generation is already running.")' in routes
    assert 'gettext("Version is not ready to publish.")' in routes
    assert "RouteError" in routes
    assert "PageChatStarted" in routes

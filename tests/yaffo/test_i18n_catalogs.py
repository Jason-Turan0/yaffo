import json
from pathlib import Path

import pytest

from yaffo.i18n import normalize_locale, text_direction
import scripts.i18n_catalogs as i18n_catalogs
from scripts.i18n_catalogs import (
    BROWSER_LOCALES_DIR,
    I18NEXT_PLACEHOLDER_RE,
    TranslationEntry,
    flatten_json,
    validate_browser_catalog,
    validate_gettext_catalog,
)

pytestmark = pytest.mark.unit


def test_normalize_locale_accepts_supported_language_variants():
    assert normalize_locale("de-DE") == "de"
    assert normalize_locale("en_US") == "en"
    assert normalize_locale("fr") is None


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
    assert validate_gettext_catalog("de", require_translated=True) == []


def test_i18next_vendor_asset_is_packaged():
    asset = Path("yaffo/static/vendor/i18next/25.7.4/i18next.min.js")
    assert asset.is_file()
    assert "i18next" in asset.read_text(encoding="utf-8")[:500]


def test_translate_missing_populates_browser_keys_and_marks_them_for_review(
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
        "_translate_batch",
        lambda entries, locale: {
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
    review = json.loads((locales_dir / "de.review.json").read_text(encoding="utf-8"))
    assert review == {"generated": ["common.greeting"]}


def test_generated_translation_must_preserve_placeholders():
    entry = TranslationEntry(
        id="browser:common.greeting",
        source="Hello {{name}}",
        context="common.greeting",
    )

    with pytest.raises(ValueError, match="placeholders differ"):
        i18n_catalogs._validate_translation(entry, "Hallo")


def test_i18next_bootstrap_treats_top_level_catalog_objects_as_namespaces():
    source = Path("yaffo/static/i18n.js").read_text(encoding="utf-8")
    assert "[locale]: catalog" in source
    assert "defaultNS: 'common'" in source


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

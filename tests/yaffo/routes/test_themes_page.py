"""Themes page: lists system + custom themes, sets the default (persisted in
ApplicationSettings, stamped on <html data-theme>), creates and deletes custom
themes, and drives the async theme-design conversation (chat / status / cancel)."""
import pytest

from yaffo import themes
from yaffo.db.models import (
    CONVERSATION_TYPE_USER,
    PAGE_VERSION_STATUS_ACCEPTED,
    PAGE_VERSION_STATUS_CANCELLED,
    PAGE_VERSION_STATUS_IN_PROGRESS,
    PAGE_VERSION_STATUS_READY,
)
from yaffo.themes import CustomTheme, ThemeAssets


def _save_ready_draft(slug="draft-me", working_accent="#abcdef") -> str:
    """A custom theme with a finished working draft awaiting publish (status READY)."""
    themes.save_custom_theme(
        CustomTheme(
            slug=slug,
            label="Draft Me",
            status=PAGE_VERSION_STATUS_READY,
            conversations=[],
            published_theme=ThemeAssets(tokens_css=f'[data-theme="{slug}"] {{\n}}\n'),
            working_theme=ThemeAssets(tokens_css=f'[data-theme="{slug}"] {{ --color-accent: {working_accent}; }}'),
        )
    )
    return slug


def test_default_theme_on_html_element(client):
    response = client.get("/themes", follow_redirects=True)
    assert response.status_code == 200
    assert b'<html lang="en" dir="ltr" data-theme="classic"' in response.data


def test_index_redirects_to_default_theme_panel(client):
    response = client.get("/themes")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/themes/classic")


def test_set_default_persists_and_renders(client):
    response = client.post("/themes/neobrutalist/default")
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"

    page = client.get("/themes/neobrutalist")
    assert b'<html lang="en" dir="ltr" data-theme="neobrutalist"' in page.data


def test_set_default_rejects_unknown_slug(client):
    response = client.post("/themes/vaporwave/default")
    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Unknown theme: vaporwave",
        "code": "theme_unknown",
    }

    page = client.get("/themes/classic")
    assert b'data-theme="classic"' in page.data


def test_show_unknown_theme_404s(client):
    assert client.get("/themes/vaporwave").status_code == 404


def test_page_lists_system_and_custom_themes(client):
    page = client.get("/themes/classic").data.decode()
    for slug, label in themes.THEMES.items():
        assert f'/themes/{slug}"' in page
        # Built-in labels are lazy_gettext (localized at render); compare resolved text.
        assert str(label) in page
    for theme in themes.list_custom_themes():
        assert f'/themes/{theme.slug}"' in page
        assert theme.label in page


def test_saved_locale_translates_themes_page(client):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    client.post("/settings/locale", data={"locale": "de"})
    page = client.get("/themes/vapor-wave").data.decode()

    assert "Designs" in page
    assert "System" in page
    assert "Benutzerdefiniert" in page
    assert "Standard festlegen" in page
    assert "Umbenennen" in page
    assert "Eine benutzerdefinierte Designvorlage" in page
    assert "Gespräch" in page
    # Built-in theme names are localized (not the English source from the registry).
    assert "Klassisch" in page
    assert "Dunkelkammer" in page
    assert "Classic" not in page
    assert "Darkroom" not in page


def test_create_custom_theme(client):
    response = client.post("/themes/create", data={"label": "Vapor Wave"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/themes/vapor-wave")

    created = themes.get_custom_theme("vapor-wave")
    assert created.label == "Vapor Wave"
    assert created.conversations == []
    assert '[data-theme="vapor-wave"]' in created.published_theme.tokens_css

    panel = client.get("/themes/vapor-wave").data.decode()
    assert "Vapor Wave" in panel
    assert "Custom" in panel


def test_create_duplicate_label_gets_unique_slug(client):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    response = client.post("/themes/create", data={"label": "Vapor Wave"})
    assert response.headers["Location"].endswith("/themes/vapor-wave-2")
    assert themes.get_custom_theme("vapor-wave-2") is not None


def test_create_requires_label(client):
    response = client.post("/themes/create", data={"label": "  "})
    assert response.status_code == 400


def test_create_validation_uses_saved_locale_and_error_code(client):
    client.post("/settings/locale", data={"locale": "de"})
    response = client.post("/themes/create", data={"label": "  "})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Designname ist erforderlich",
        "code": "theme_name_required",
    }


def test_delete_custom_theme(client):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    response = client.post("/themes/vapor-wave/delete")
    assert response.status_code == 302
    assert themes.get_custom_theme("vapor-wave") is None


def test_delete_active_custom_theme_falls_back_to_default(client):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    client.post("/themes/vapor-wave/default")
    client.post("/themes/vapor-wave/delete")
    assert themes.get_theme() == themes.DEFAULT_THEME


def test_delete_system_theme_rejected(client):
    response = client.post("/themes/classic/delete")
    assert response.status_code == 400
    assert response.get_json()["code"] == "system_theme_locked"
    assert themes.theme_exists("classic")


def test_settings_page_no_longer_has_theme_control(client):
    page = client.get("/settings").data.decode()
    assert "theme-select" not in page
    assert "Appearance" not in page


# --- async theme-design conversation: chat / status / cancel -----------------

@pytest.fixture
def with_key_and_task(monkeypatch):
    """Configure an API key and capture the enqueued generation task instead of
    handing it to the task queue, so the route's record-prompt + enqueue is exercised without
    a worker. Returns the list of (args, kwargs) the task was called with."""
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda *a, **k: "test-key")
    calls = []
    monkeypatch.setattr("yaffo.routes.themes_page.generate_theme_task",
                        lambda *a, **k: calls.append((a, k)))
    return calls


def test_chat_records_prompt_sets_running_and_enqueues(client, with_key_and_task):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    response = client.post("/themes/vapor-wave/chat", json={"message": "make it neon"})
    assert response.status_code == 202
    assert response.get_json() == {"slug": "vapor-wave"}

    theme = themes.get_custom_theme("vapor-wave")
    assert theme.status == PAGE_VERSION_STATUS_IN_PROGRESS
    assert [(c.type, c.content) for c in theme.conversations] == [
        (CONVERSATION_TYPE_USER, "make it neon")
    ]
    assert with_key_and_task == [(("vapor-wave", "make it neon"), {})]


def test_chat_requires_message(client, with_key_and_task):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    response = client.post("/themes/vapor-wave/chat", json={"message": "  "})
    assert response.status_code == 400
    assert with_key_and_task == []


def test_chat_validation_uses_saved_locale_and_error_code(client, with_key_and_task):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    client.post("/settings/locale", data={"locale": "de"})
    response = client.post("/themes/vapor-wave/chat", json={"message": "  "})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Nachricht ist erforderlich.",
        "code": "message_required",
    }
    assert with_key_and_task == []


def test_chat_without_api_key_is_400(client):
    # The autouse no_api_key fixture leaves generation disabled.
    client.post("/themes/create", data={"label": "Vapor Wave"})
    response = client.post("/themes/vapor-wave/chat", json={"message": "make it neon"})
    assert response.status_code == 400
    assert "API key" in response.get_json()["error"]


def test_chat_rejects_concurrent_run(client, with_key_and_task):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    client.post("/themes/vapor-wave/chat", json={"message": "first"})
    response = client.post("/themes/vapor-wave/chat", json={"message": "second"})
    assert response.status_code == 409
    assert len(with_key_and_task) == 1  # the second prompt was not enqueued


def test_chat_on_builtin_theme_rejected(client, with_key_and_task):
    response = client.post("/themes/classic/chat", json={"message": "hi"})
    assert response.status_code == 400
    assert response.get_json()["code"] == "system_theme_locked"
    assert with_key_and_task == []


def test_chat_unknown_theme_404s(client, with_key_and_task):
    assert client.post("/themes/nope/chat", json={"message": "hi"}).status_code == 404


def test_status_returns_feed_and_started_at(client, with_key_and_task):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    client.post("/themes/vapor-wave/chat", json={"message": "make it neon"})

    payload = client.get("/themes/vapor-wave/status").get_json()
    assert payload["slug"] == "vapor-wave"
    assert payload["status"] == PAGE_VERSION_STATUS_IN_PROGRESS
    assert payload["messages"] == [{"type": CONVERSATION_TYPE_USER, "content": "make it neon"}]
    assert payload["started_at"] is not None  # the prompt timestamp drives the elapsed counter


def test_status_unknown_theme_404s(client):
    assert client.get("/themes/nope/status").status_code == 404


def test_cancel_flags_cancelled(client, with_key_and_task):
    client.post("/themes/create", data={"label": "Vapor Wave"})
    client.post("/themes/vapor-wave/chat", json={"message": "make it neon"})

    response = client.post("/themes/vapor-wave/cancel")
    assert response.status_code == 204
    assert themes.get_custom_theme("vapor-wave").status == PAGE_VERSION_STATUS_CANCELLED


def test_cancel_unknown_theme_404s(client):
    assert client.post("/themes/nope/cancel").status_code == 404


# --- publish / discard a finished draft --------------------------------------

def test_publish_promotes_working_into_published(client):
    slug = _save_ready_draft()
    response = client.post(f"/themes/{slug}/publish")
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"

    theme = themes.get_custom_theme(slug)
    assert theme.status == PAGE_VERSION_STATUS_ACCEPTED
    assert theme.working_theme is None
    assert "--color-accent: #abcdef;" in theme.published_theme.tokens_css


def test_published_theme_css_reflects_published_draft(client):
    slug = _save_ready_draft()
    client.post(f"/themes/{slug}/publish")
    css = client.get(f"/themes/{slug}/theme.css").get_data(as_text=True)
    assert "--color-accent: #abcdef;" in css  # the served CSS is now the promoted draft


def test_publish_without_draft_is_409(client):
    client.post("/themes/create", data={"label": "No Draft"})  # ACCEPTED, no working draft
    response = client.post("/themes/no-draft/publish")
    assert response.status_code == 409


def test_publish_validation_uses_saved_locale_and_error_code(client):
    client.post("/themes/create", data={"label": "No Draft"})
    client.post("/settings/locale", data={"locale": "de"})
    response = client.post("/themes/no-draft/publish")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "Kein Designentwurf zum Veröffentlichen vorhanden.",
        "code": "theme_draft_missing",
    }


def test_publish_unknown_theme_404s(client):
    assert client.post("/themes/nope/publish").status_code == 404


def test_discard_drops_draft_and_keeps_published(client):
    slug = _save_ready_draft()
    published_before = themes.get_custom_theme(slug).published_theme.tokens_css

    response = client.post(f"/themes/{slug}/discard")
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"

    theme = themes.get_custom_theme(slug)
    assert theme.status == PAGE_VERSION_STATUS_ACCEPTED
    assert theme.working_theme is None
    assert theme.published_theme.tokens_css == published_before


def test_discard_unknown_theme_404s(client):
    assert client.post("/themes/nope/discard").status_code == 404


def test_page_shows_draft_controls_only_when_ready(client):
    slug = _save_ready_draft()
    page = client.get(f"/themes/{slug}").data.decode()
    assert "Save draft" in page
    assert f"/themes/{slug}/publish" in page
    assert f"/themes/{slug}/discard" in page

    # once published, the draft banner is gone.
    client.post(f"/themes/{slug}/publish")
    page = client.get(f"/themes/{slug}").data.decode()
    assert "Save draft" not in page

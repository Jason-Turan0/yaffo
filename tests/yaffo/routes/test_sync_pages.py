"""Route tests for sync automations: creating one from the New automation dialog, the
setup sections, consent, Test, Items, the filter screen, and how runs read in the
run history."""
import json
from pathlib import Path

import pytest
from PIL import Image

from yaffo.background_tasks.tasks import sync_run
from yaffo.db import db
from yaffo.db.models import (
    Album, AlbumItem, ApplicationSettings, Automation, AutomationTrigger, MediaItem, SyncAdapter, SyncItem,
    EVENT_ALBUM_CHANGED, MEDIA_STATUS_INDEXED,
)
from yaffo.db.repositories import sync_repository

pytestmark = pytest.mark.unit

MEDIA_DIR_ID = "6f1c0000-0000-4000-8000-0000000000aa"


@pytest.fixture
def library(app, tmp_path) -> Path:
    root = tmp_path / "Pictures"
    root.mkdir()
    db.session.add_all([
        SyncAdapter(slug="builtin.folder_source", name="Folder", role="source", provider="local", is_preset=True,
                    handler="folder_source", grants=[], doc_domains=[]),
        SyncAdapter(slug="builtin.folder_target", name="Folder", role="target", provider="local", is_preset=True,
                    handler="folder_target", grants=[], doc_domains=[]),
        ApplicationSettings(name="media_dirs", type="json",
                            value=json.dumps([{"id": MEDIA_DIR_ID, "path": str(root)}])),
        ApplicationSettings(name="thumbnail_dir", type="string", value=str(tmp_path / "thumbs")),
    ])
    db.session.commit()
    return root


@pytest.fixture
def mom(app, library) -> Album:
    photo = library / "a.jpg"
    Image.new("RGB", (8, 8), (9, 9, 9)).save(photo)
    media = MediaItem(full_file_path=str(photo), status=MEDIA_STATUS_INDEXED, date_taken="2024-07-04T10:00:00",
                      year=2024, month=7)
    album = Album(name="For Mom")
    db.session.add_all([media, album])
    db.session.flush()
    db.session.add(AlbumItem(album_id=album.id, media_item_id=media.id, position=0))
    db.session.commit()
    return album


@pytest.fixture
def outbox(tmp_path) -> Path:
    folder = tmp_path / "Backup"
    folder.mkdir()
    return folder


def _adapter_id(role: str) -> int:
    return db.session.query(SyncAdapter.id).filter(SyncAdapter.role == role, SyncAdapter.handler.isnot(None)).scalar()


def _create(client, kind: str, name: str) -> Automation:
    resp = client.post("/utilities/automations/create",
                       data={"name": name, "kind": kind,
                             "adapter_id": _adapter_id("source" if kind == "import" else "target")})
    assert resp.status_code == 302
    return db.session.query(Automation).filter_by(name=name).one()


def _ready_target(client, mom, outbox) -> Automation:
    automation = _create(client, "send", "For Mom backup")
    client.post(f"/utilities/automations/{automation.slug}/sync/options", data={"folder": str(outbox)})
    client.post(f"/utilities/automations/{automation.slug}/sync/selection", data={"kind": "album", "album_id": mom.id})
    client.post(f"/utilities/automations/{automation.slug}/sync/turn-on")
    db.session.refresh(automation)
    return automation


def test_sync_detail_embeds_trigger_panel_above_run_history(client, library):
    automation = _create(client, "import", "Camera card")
    body = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert 'id="automation-trigger-panel"' in body
    # Folder and options, Where to put them, When, and Items.
    assert body.count('class="settings-section sync-step collapsible-panel"') == 4
    assert "data-cron-builder" in body
    assert body.index('id="automation-trigger-panel"') < body.index('id="automation-runs"')
    assert 'btn-secondary btn-sm">Edit triggers</span>' not in body


def test_the_new_automation_dialog_offers_import_and_send(client, library):
    body = client.get("/utilities/automations").get_data(as_text=True)

    assert 'value="import"' in body and 'value="send"' in body
    send = body.split('data-sync-services="send"')[1].split("</div>\n        </div>")[0]
    assert 'name="adapter_id"' in send and "Folder" in send


def test_creating_a_send_sync_opens_its_setup(client, library):
    automation = _create(client, "send", "For Mom backup")

    body = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)

    assert automation.is_sync and not automation.enabled
    assert "Folder and options" in body and "What to send" in body and "To do" in body
    assert "Remove from destination" in body and "Keep at destination" in body
    assert 'data-tooltip="Remove moves only unchanged copies Yaffo made to the Trash. Keep leaves them at the destination."' in body
    assert "Delete Yaffo&#39;s copy" not in body
    enable_form = body.split(f'action="/utilities/automations/{automation.slug}/sync/turn-on"')[1].split('</form>')[0]
    assert 'disabled>Enable</button>' in enable_form
    triggers = {(t.trigger_type, t.event_type, t.cron) for t in automation.triggers}
    assert ("schedule", None, "0 2 * * *") in triggers and ("event", "media_deleted", None) in triggers


def test_an_import_sync_starts_with_an_import_folder(client, library):
    automation = _create(client, "import", "Camera card")

    config = sync_repository.load_sync(automation).config
    assert config.import_root == {"media_dir_id": MEDIA_DIR_ID, "subpath": "Imports/Camera card",
                                  "layout": "source_folders"}


def test_import_folder_help_is_an_info_hover(client, library):
    automation = _create(client, "import", "Camera card")
    body = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)

    assert 'class="help-tip" data-icon="info"' in body
    assert 'data-tooltip="Want to keep these photos where they are? Add the folder as a media folder in Settings instead. New photos are copied from here. Nothing in it is changed or deleted."' in body
    assert '<p class="hint-text">New photos are copied from here.' not in body


def test_import_destination_help_is_an_info_hover(client, library):
    automation = _create(client, "import", "Camera card")
    body = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)

    help_text = "New photos go into this folder of your library."
    assert f'data-tooltip="{help_text}"' in body
    assert f'<p class="section-description">{help_text}</p>' not in body
    # How they're arranged is its own choice, keeping the source's folders by default.
    assert '<option value="source_folders" selected>Keep the source&#39;s folders</option>' in body


def test_import_subtitle_names_the_source_without_repeating_the_destination(client, library):
    automation = _create(client, "import", "Camera card")
    source = library.parent / "Camera card source"
    source.mkdir()
    client.post(f"/utilities/automations/{automation.slug}/sync/options", data={"folder": str(source)})

    body = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)

    assert f"Imports new photos from {source}" in body
    assert f"From {source} into" not in body


def test_a_service_is_required(client, library):
    resp = client.post("/utilities/automations/create", data={"name": "X", "kind": "send", "adapter_id": "999"})
    assert resp.status_code == 400


def test_a_folder_inside_the_library_is_explained(client, library):
    automation = _create(client, "send", "Loop")

    client.post(f"/utilities/automations/{automation.slug}/sync/options", data={"folder": str(library / "x")})
    body = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)

    assert "That folder is inside your library. Choose one outside it." in body
    # Nothing to test or enable until the folder is fixed.
    assert 'id="sync-test"' not in body
    enable_form = body.split(f'action="/utilities/automations/{automation.slug}/sync/turn-on"')[1].split('</form>')[0]
    assert 'disabled>Enable</button>' in enable_form


def test_choosing_an_album_creates_its_change_trigger_and_turning_on_records_consent(client, mom, outbox):
    automation = _ready_target(client, mom, outbox)

    trigger = db.session.query(AutomationTrigger).filter_by(automation_id=automation.id,
                                                            event_type=EVENT_ALBUM_CHANGED).one()
    assert trigger.config["album_id"] == mom.id
    assert automation.enabled
    body = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert '<h1>For Mom backup</h1>' in body
    assert 'page-header-title-badge' not in body
    assert f'action="/utilities/automations/{automation.slug}/sync/turn-off"' in body
    assert "Disable" in body and "Review and turn on" not in body
    assert "Since" in body


def test_changing_the_folder_turns_the_sync_off(client, mom, outbox, tmp_path):
    automation = _ready_target(client, mom, outbox)
    (tmp_path / "elsewhere").mkdir()

    client.post(f"/utilities/automations/{automation.slug}/sync/options", data={"folder": str(tmp_path / "elsewhere")})

    db.session.refresh(automation)
    assert not automation.enabled
    body = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert f'action="/utilities/automations/{automation.slug}/sync/turn-on"' in body
    assert 'page-header-title-badge' not in body
    # The generic toggle cannot bypass sync setup consent.
    resp = client.post(f"/utilities/automations/{automation.slug}/enabled")
    assert resp.status_code == 409
    db.session.refresh(automation)
    assert not automation.enabled


def test_a_ready_sync_has_enable_in_the_header_without_a_review_panel(client, mom, outbox):
    automation = _create(client, "send", "For Mom backup")
    client.post(f"/utilities/automations/{automation.slug}/sync/options", data={"folder": str(outbox), "convert": "jpeg"})
    client.post(f"/utilities/automations/{automation.slug}/sync/selection", data={"kind": "album", "album_id": mom.id})

    body = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)

    enable_form = body.split(f'action="/utilities/automations/{automation.slug}/sync/turn-on"')[1].split('</form>')[0]
    assert '>Enable</button>' in enable_form
    assert 'disabled>Enable</button>' not in enable_form
    assert 'id="sync-step-consent"' not in body
    assert 'page-header-title-badge' not in body


def test_test_shows_what_a_run_would_do_without_doing_it(client, mom, outbox):
    automation = _ready_target(client, mom, outbox)

    data = client.post(f"/utilities/automations/{automation.slug}/sync/test").get_json()

    assert (data["would_add"], data["would_remove"], data["problem"]) == (1, 0, None)
    assert data["add_samples"] == ["a.jpg"]
    assert list(outbox.iterdir()) == []


def test_items_and_run_history_after_a_run(client, mom, outbox):
    automation = _ready_target(client, mom, outbox)
    sync_run.run_and_record_sync(db.session, automation, "run-1")

    items = client.get(f"/utilities/automations/{automation.slug}/sync/items").get_data(as_text=True)
    page = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)

    assert "Written to folder" in items and "For Mom/a.jpg" in items
    assert '<div class="table-container">' in items and '<table class="data-table sync-items-table">' in items
    assert "Sent to" in items and "Showing 1–1 of 1 results" in items
    assert "Wrote 1 photo to the folder" in page
    # The Items panel folds to its counts.
    assert 'id="sync-items"' in page and "1 written to folder" in page


def test_the_filter_screen_saves_the_filters_as_the_selection(client, mom, outbox):
    automation = _create(client, "send", "2024")

    screen = client.get(f"/utilities/automations/{automation.slug}/sync/filter?year=2024")
    resp = client.post(f"/utilities/automations/{automation.slug}/sync/selection?year=2024", data={"kind": "query"})

    assert screen.status_code == 200 and "1 photo matches" in screen.get_data(as_text=True)
    assert resp.status_code == 302
    selection = sync_repository.load_sync(automation).config.selection
    assert (selection.kind, selection.query) == ("query", {"year": 2024})


def test_an_unfiltered_selection_is_refused(client, mom, outbox):
    automation = _create(client, "send", "Everything")

    client.post(f"/utilities/automations/{automation.slug}/sync/selection", data={"kind": "query"})

    assert sync_repository.load_sync(automation).config.selection is None


def test_deleting_a_sync_removes_its_ledger_but_not_the_copies(client, mom, outbox):
    automation = _ready_target(client, mom, outbox)
    sync_run.run_and_record_sync(db.session, automation, "run-1")

    client.post(f"/utilities/automations/{automation.slug}/delete")

    assert db.session.query(SyncItem).count() == 0
    assert [p.name for p in (outbox / "For Mom").iterdir()] == ["a.jpg"]


def test_items_search_status_and_paging(client, library, monkeypatch):
    from yaffo.routes.utilities import sync_views

    monkeypatch.setattr(sync_views, "ITEMS_PAGE_SIZE", 2)
    monkeypatch.setattr(sync_views, "ITEMS_PAGE_SIZES", (2, 25))
    automation = _create(client, "import", "Card")
    photos = []
    for name in ("beach_1.jpg", "beach_2.jpg", "beach_3.jpg", "party.jpg"):
        path = library / "Imports" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 4)).save(path)
        photos.append(MediaItem(full_file_path=str(path), status=MEDIA_STATUS_INDEXED))
    db.session.add_all(photos)
    db.session.flush()
    for index, photo in enumerate(photos):
        db.session.add(SyncItem(automation_id=automation.id, item_key=f"k{index}", media_item_id=photo.id,
                                remote_id=f"k{index}", status="imported", attempts=1))
    db.session.add(SyncItem(automation_id=automation.id, item_key="gone", remote_id="gone",
                            status="failed", error_code="import_failed", details={"filename": "beach_lost.heic"}))
    db.session.commit()
    url = f"/utilities/automations/{automation.slug}/sync/items"

    first = client.get(url).get_data(as_text=True)
    assert "Showing 1–2 of 5 results" in first and "All · 5" in first and "Page 1 of 3" in first

    beach = client.get(f"{url}?q=BEACH").get_data(as_text=True)
    assert "All · 4" in beach and "party.jpg" not in beach
    assert "Failed · 1" in beach and "Imported · 3" in beach

    failed = client.get(f"{url}?q=beach&status=failed").get_data(as_text=True)
    assert "beach_lost.heic" in failed and "Showing 1–1 of 1 results" in failed and "Retry failed" in failed

    page2 = client.get(f"{url}?q=beach&status=imported&page=1").get_data(as_text=True)
    assert "Showing 3–3 of 3 results" in page2 and "Page 2 of 2" in page2
    bigger = client.get(f"{url}?page-size=25").get_data(as_text=True)
    assert "Showing 1–5 of 5 results" in bigger
    # A page size the panel doesn't offer falls back to the default.
    assert "Showing 1–2 of 5 results" in client.get(f"{url}?page-size=7").get_data(as_text=True)

    # Folder names in the path don't match: only the file name is searched.
    assert "No items match" in client.get(f"{url}?q=Imports").get_data(as_text=True)
    # LIKE wildcards are taken literally.
    assert "No items match" in client.get(f"{url}?q=%25").get_data(as_text=True)

    page = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert "1 failed · 4 imported" in page
    assert '<details class="settings-section sync-step collapsible-panel" id="sync-items" open>' in page


def test_status_chips_keep_a_fixed_order_and_the_selected_one(client, library):
    automation = _create(client, "import", "Ordered")
    for index, status in enumerate(["linked_existing", "imported", "deleted_at_source", "failed", "imported"]):
        db.session.add(SyncItem(automation_id=automation.id, item_key=f"k{index}", remote_id=f"k{index}",
                                status=status, attempts=1, details={"filename": f"photo_{index}.jpg"}))
    db.session.commit()
    url = f"/utilities/automations/{automation.slug}/sync/items"

    body = client.get(url).get_data(as_text=True)
    labels = ["Failed · 1", "Imported · 2", "Already in library · 1", "Deleted at source · 1"]
    positions = [body.index(label) for label in labels]
    assert positions == sorted(positions)
    page = client.get(f"/utilities/automations/{automation.slug}").get_data(as_text=True)
    assert "1 failed · 2 imported · 1 already in library · 1 deleted at source" in page

    # Filtered to Failed, then a search with no failed matches: the Failed chip stays,
    # selected, at 0, and the empty result says why.
    narrowed = client.get(f"{url}?status=failed&q=photo_1").get_data(as_text=True)
    assert 'aria-pressed="true"' in narrowed and "Failed · 0" in narrowed
    assert narrowed.index("Failed · 0") < narrowed.index("Imported · 1")
    assert "No items match" in narrowed

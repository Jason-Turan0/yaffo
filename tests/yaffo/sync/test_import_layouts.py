"""How an import arranges new files under its import root: keep the source's folders
(the default), by year and month, by year, or all in one folder. Through the real
engine, for the Folder source and the OneDrive preset (against the fake Graph)."""
from pathlib import Path

import pytest

from yaffo.db.repositories import sync_repository
from yaffo.sync import setup
from yaffo.sync.engine import run_sync
from yaffo.sync.source_run import destination_folder
from yaffo.sync.schemas import LAYOUT_FLAT, LAYOUT_SOURCE_FOLDERS, LAYOUT_YEAR, LAYOUT_YEAR_MONTH

from .conftest import MEDIA_DIR_ID, write_jpeg


@pytest.fixture
def card(tmp_path) -> Path:
    root = tmp_path / "card"
    write_jpeg(root / "DCIM" / "100CANON" / "IMG_0001.JPG", "2024:07:04 10:00:00", color=(1, 2, 3))
    write_jpeg(root / "DCIM" / "101CANON" / "IMG_0002.JPG", "2023:01:15 10:00:00", color=(4, 5, 6))
    write_jpeg(root / "loose.jpg", "2024:08:01 10:00:00", color=(7, 8, 9))
    return root


def imported(library: Path) -> list[str]:
    root = library / "Imports" / "Card"
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("layout, expected", [
    (LAYOUT_SOURCE_FOLDERS, ["DCIM/100CANON/IMG_0001.JPG", "DCIM/101CANON/IMG_0002.JPG", "loose.jpg"]),
    (LAYOUT_YEAR_MONTH, ["2023/01/IMG_0002.JPG", "2024/07/IMG_0001.JPG", "2024/08/loose.jpg"]),
    (LAYOUT_YEAR, ["2023/IMG_0002.JPG", "2024/IMG_0001.JPG", "2024/loose.jpg"]),
    (LAYOUT_FLAT, ["IMG_0001.JPG", "IMG_0002.JPG", "loose.jpg"]),
])
def test_folder_source_layouts(session, library, card, run_context, layout, expected):
    automation = setup.create_sync(session, "Card", adapter_id=1)
    setup.set_options(session, automation, {"folder": str(card)})
    setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Card", layout)
    setup.turn_on(session, automation)
    result = run_sync(session, automation.id, run_context)
    assert result.problem is None
    assert imported(library) == expected


def test_new_syncs_keep_the_source_folders(session, library):
    automation = setup.create_sync(session, "Card", adapter_id=1)
    setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Card")
    assert sync_repository.load_sync(automation).config.import_root["layout"] == LAYOUT_SOURCE_FOLDERS


def test_changing_the_layout_needs_approval_again(session, library, card):
    automation = setup.create_sync(session, "Card", adapter_id=1)
    setup.set_options(session, automation, {"folder": str(card)})
    setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Card")
    setup.turn_on(session, automation)
    setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Card", LAYOUT_FLAT)
    assert not automation.enabled
    with pytest.raises(ValueError):
        setup.set_import_root(session, automation, MEDIA_DIR_ID, "Imports/Card", "by_colour")


def test_a_source_folder_can_never_climb_out_of_the_import_root(tmp_path):
    record = {"folder": "../../etc/./a%2F..%2Fb/ c "}
    assert destination_folder(LAYOUT_SOURCE_FOLDERS, tmp_path / "x.jpg", record) == ("etc", "a", "b", "c")
    assert destination_folder(LAYOUT_SOURCE_FOLDERS, tmp_path / "x.jpg", {}) == ()


def test_onedrive_keeps_its_folders_with_few_lookups(session, library, onedrive_sync, fake_onedrive, run_context):
    fake_onedrive.state.add_photos(12)   # every 4th lands in Camera Roll/2024
    automation = onedrive_sync()
    assert run_sync(session, automation.id, run_context).problem is None
    root = library / "Imports" / "OneDrive"
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert files == sorted([f"IMG_{n:05d}.jpg" for n in range(1, 13) if n % 4]
                           + [f"2024/IMG_{n:05d}.jpg" for n in range(1, 13) if n % 4 == 0])
    # The subfolder's path came from the delta listing itself: only the chosen
    # folder needed a lookup.
    assert fake_onedrive.state.counters["item_lookups"] == 1

    # The next run reuses the remembered paths.
    fake_onedrive.state.add_photos(4)
    assert run_sync(session, automation.id, run_context).params["imported"] == 4
    assert (root / "2024" / "IMG_00016.jpg").is_file()
    assert fake_onedrive.state.counters["item_lookups"] == 1

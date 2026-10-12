"""A custom target rule reads the library and its published code is consented."""
import pytest

from yaffo.sync import setup
from yaffo.sync.adapters import load_adapter
from yaffo.sync.consent import has_consent
from yaffo.sync.schemas import SELECTION_SCRIPT, Selection
from yaffo.sync.selection import resolve_selection
from yaffo.db.repositories import sync_repository

pytestmark = pytest.mark.unit


def test_script_selects_photo_with_a_target_and_caption(session, add_photo):
    photo = add_photo("a.jpg")
    code = f'[{{"media_item_id": {photo.id}, "target": "Family", "caption": "At the lake"}}]'

    desired = resolve_selection(session, Selection(kind=SELECTION_SCRIPT), "unused", code)

    assert [(d.media_item_id, d.target, d.caption) for d in desired] == [
        (photo.id, "Family", "At the lake")]


def test_script_cannot_call_mutating_host_functions(session, add_photo):
    photo = add_photo("a.jpg")
    code = f'tag_media_items([{{"media_item_id": {photo.id}, "name": "sent"}}])\n[]'

    with pytest.raises(ValueError, match="custom rule failed"):
        resolve_selection(session, Selection(kind=SELECTION_SCRIPT), "unused", code)


def test_published_rule_change_invalidates_sync_consent(session, add_photo, album, tmp_path):
    photo = add_photo("a.jpg")
    target = setup.create_sync(session, "Script target", adapter_id=2)
    setup.set_options(session, target, {"folder": str(tmp_path)})
    setup.set_selection(session, target, Selection(kind=SELECTION_SCRIPT))
    target.published_code = f'[{{"media_item_id": {photo.id}, "target": "Family"}}]'
    session.commit()
    setup.turn_on(session, target)
    config = sync_repository.load_sync(target).config
    adapter = load_adapter(session, config.adapter_id)
    assert has_consent(config, adapter, target.published_code)

    target.published_code = f'[{{"media_item_id": {photo.id}, "target": "Friends"}}]'
    session.commit()

    assert not has_consent(config, adapter, target.published_code)

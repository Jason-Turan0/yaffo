"""Route tests for the Index Photos page: the page renders without scanning, and the
scan endpoint streams the NDJSON contract (progress records, then a done record)."""
import json

import pytest

from yaffo.utils.file_sync import MediaScan

pytestmark = pytest.mark.unit


def test_page_renders_shell_without_scanning(app, client, monkeypatch, tmp_path):
    # The GET must not run the (slow) scan — guard by making iter_media_scan explode
    # if it's ever called during the page render.
    monkeypatch.setattr(
        "yaffo.routes.utilities.index_photos.iter_media_scan",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("page render must not scan")),
    )
    media = tmp_path / "organized"
    media.mkdir()
    monkeypatch.setattr("yaffo.routes.utilities.index_photos.get_media_dirs", lambda: [media])
    monkeypatch.setattr("yaffo.routes.utilities.index_photos.get_thumbnail_dir", lambda: tmp_path / "thumbs")

    resp = client.get("/utilities/index-photos")
    assert resp.status_code == 200
    assert b'id="scan-results"' in resp.data        # shell present for JS to fill
    assert b'id="stat-total-filesystem"' in resp.data


def test_scan_stream_emits_progress_then_done(app, client, monkeypatch):
    fake = MediaScan(
        unindexed=[{"filename": "b.jpg", "full_path": "/m/b.jpg"}],
        orphaned=[],
        total_imported=2,
        total_indexed=1,
        total_filesystem=2,
    )
    monkeypatch.setattr(
        "yaffo.routes.utilities.index_photos.iter_media_scan",
        lambda *a, **k: iter([7, fake]),
    )

    resp = client.get("/utilities/index-photos/scan")
    assert resp.status_code == 200
    assert resp.mimetype == "application/x-ndjson"
    assert resp.headers.get("Cache-Control") == "no-store"  # live data, never cached

    records = [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]
    assert records[0] == {"type": "progress", "scanned": 7}
    assert records[1]["type"] == "done"
    assert records[1]["total_filesystem"] == 2
    assert records[1]["unindexed"] == [{"filename": "b.jpg", "full_path": "/m/b.jpg"}]
    assert records[1]["orphaned"] == []


def test_scan_stream_reports_error_as_record(app, client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk unplugged")
        yield  # make it a generator

    monkeypatch.setattr("yaffo.routes.utilities.index_photos.iter_media_scan", boom)

    resp = client.get("/utilities/index-photos/scan")
    assert resp.status_code == 200
    records = [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]
    assert records[-1]["type"] == "error"
    assert "disk unplugged" in records[-1]["message"]

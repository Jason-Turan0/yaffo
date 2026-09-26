import os
import threading
import time
from pathlib import Path

import pytest

from yaffo.site_agents.assistant import fs as fs_module
from yaffo.site_agents.assistant.fs import AssistantFS, FsError, FsTimeout, run_with_timeout

pytestmark = pytest.mark.unit


@pytest.fixture
def library(tmp_path):
    media = tmp_path / "media"
    (media / "2019" / "08").mkdir(parents=True)
    (media / "2019" / "08" / "a.jpg").write_bytes(b"x" * 10)
    (media / "2019" / "b.jpg").write_bytes(b"y")
    (media / "yaffo.db").write_bytes(b"db")
    (media / "config.toml").write_text("secret")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("nope")
    os.symlink(outside, media / "escape")
    data = tmp_path / "data"
    data.mkdir()
    return AssistantFS({"m1": media}, data_dir=data, timeout=2.0), media, data


def test_stat_and_list_inside_a_media_dir(library):
    fs, _, _ = library
    stat = fs.stat("m1", "2019/08/a.jpg")
    assert stat.exists and not stat.is_dir and stat.size == 10 and stat.extension == ".jpg"
    assert fs.stat("m1", "2019/missing.jpg").exists is False

    listing = fs.list_dir("m1", "2019")
    assert listing.directories == ["08"] and listing.files == ["b.jpg"]


def test_root_listing_hides_denied_names(library):
    fs, _, _ = library
    listing = fs.list_dir("m1", "")
    assert "yaffo.db" not in listing.files and "config.toml" not in listing.files
    assert listing.denied == 2


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside/private.txt", "2019/../../outside", "~/x", "C:/x"])
def test_absolute_and_parent_paths_are_refused(library, path):
    fs, _, _ = library
    with pytest.raises(FsError):
        fs.stat("m1", path)


def test_symlink_escape_is_refused(library):
    fs, _, _ = library
    with pytest.raises(FsError, match="outside"):
        fs.list_dir("m1", "escape")


def test_deny_listed_names_are_refused_inside_the_root(library):
    fs, _, _ = library
    for name in ("yaffo.db", "config.toml", "keys/id_rsa", ".ssh/known_hosts"):
        with pytest.raises(FsError, match="not allowed"):
            fs.stat("m1", name)


def test_unknown_media_dir(library):
    fs, _, _ = library
    with pytest.raises(FsError, match="no media folder"):
        fs.stat("nope", "")


def test_listing_is_capped(library):
    fs, media, _ = library
    for i in range(30):
        (media / "2019" / f"f{i:02}.jpg").write_bytes(b"")
    listing = fs.list_dir("m1", "2019", limit=5)
    assert len(listing.directories) + len(listing.files) == 5
    assert listing.truncated and listing.total_files == 31


def test_tail_log_by_name_only(library):
    fs, _, data = library
    (data / "background_tasks.log").write_text("\n".join(f"line {i}" for i in range(1000)) + "\n")
    (data / "background_tasks.log.1").write_text("old ERROR match\n")
    tail = fs.tail_log("background_tasks.log", 3)
    assert tail.lines == ["line 997", "line 998", "line 999"]
    filtered = fs.tail_log("background_tasks.log", 10, contains="error")
    assert filtered.lines == ["old ERROR match"]
    with pytest.raises(FsError, match="unknown log"):
        fs.tail_log("../yaffo.db", 3)


def test_binary_log_is_refused(library):
    fs, _, data = library
    (data / "yaffo.log").write_bytes(b"\x00\x01binary")
    with pytest.raises(FsError, match="not a text file"):
        fs.tail_log("yaffo.log", 5)


def test_log_lines_are_clipped(library):
    fs, _, data = library
    (data / "yaffo.log").write_text("x" * 5000 + "\n")
    assert len(fs.tail_log("yaffo.log", 1).lines[0]) == fs_module.MAX_LINE_CHARS + 1


def test_slow_disk_times_out_instead_of_hanging():
    release = threading.Event()
    started = time.monotonic()
    with pytest.raises(FsTimeout, match="did not respond"):
        run_with_timeout(lambda: release.wait(10), 0.2)
    assert time.monotonic() - started < 2
    release.set()


def test_probe_reports_an_unresponsive_drive(library, monkeypatch):
    fs, _, _ = library
    fs.timeout = 0.2
    release = threading.Event()
    monkeypatch.setattr(Path, "stat", lambda self, **kw: release.wait(10))
    probe = fs.probe("m1")
    release.set()
    assert probe.responded is False and "did not respond" in probe.error


def test_missing_media_dir_facts(tmp_path):
    fs = AssistantFS({"gone": tmp_path / "unmounted"}, data_dir=tmp_path)
    assert fs.media_dir_facts("gone") == {"exists": False}
    with pytest.raises(FsError, match="not available"):
        fs.list_dir("gone", "")

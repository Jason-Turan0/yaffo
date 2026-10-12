"""sweep_temps: a sync's leftover temporary files go at run start, but a resumable
download's partial is kept until it's days old."""
import os
import time

import pytest

from yaffo.sync.files import PARTIAL_MAX_AGE_SECONDS, stable_temp_path, sweep_temps

pytestmark = pytest.mark.unit


def test_partials_are_kept_until_they_are_old(tmp_path):
    temp = stable_temp_path(tmp_path, "27", "md1:a.jpg")
    finished = temp
    young_partial = temp.with_name(temp.name + ".partial")
    young_sidecar = temp.with_name(temp.name + ".partial.json")
    old = stable_temp_path(tmp_path, "27", "md1:b.jpg")
    old_partial = old.with_name(old.name + ".partial")
    for path in (finished, young_partial, young_sidecar, old_partial):
        path.write_bytes(b"x")
    other = tmp_path / ".yaffo-28-abc.part.partial"
    other.write_bytes(b"x")
    now = time.time()
    os.utime(old_partial, (now - PARTIAL_MAX_AGE_SECONDS - 10,) * 2)

    removed = sweep_temps(tmp_path, "27", now=now)

    assert removed == 2
    assert not finished.exists() and not old_partial.exists()
    assert young_partial.exists() and young_sidecar.exists() and other.exists()  # other syncs' files untouched


def test_stable_names_depend_only_on_the_sync_and_record(tmp_path):
    assert stable_temp_path(tmp_path, "27", "md1:a.jpg") == stable_temp_path(tmp_path, "27", "md1:a.jpg")
    assert stable_temp_path(tmp_path, "27", "md1:a.jpg") != stable_temp_path(tmp_path, "27", "md1:b.jpg")

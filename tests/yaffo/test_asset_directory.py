from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


def test_asset_directory_can_be_separate_from_mutable_data(tmp_path):
    data_dir = tmp_path / "data"
    asset_dir = tmp_path / "read-only-assets"
    environment = {
        **os.environ,
        "YAFFO_DATA_DIR": str(data_dir),
        "YAFFO_ASSET_DIR": str(asset_dir),
    }
    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            (
                "from yaffo.common import DB_PATH, FFMPEG_DIR, MODEL_CACHE_DIR; "
                "print(DB_PATH); print(MODEL_CACHE_DIR); print(FFMPEG_DIR)"
            ),
        ],
        env=environment,
        text=True,
    ).splitlines()

    assert output == [
        str(data_dir / "yaffo.db"),
        str(asset_dir / "models"),
        str(asset_dir / "ffmpeg"),
    ]

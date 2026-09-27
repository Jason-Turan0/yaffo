"""Which indexing failures are permanent (the item is marked FAILED and file sync
leaves it alone until the file changes) and which are retried at the next sync."""
import errno

import pytest
from PIL import UnidentifiedImageError

from yaffo.utils.index_errors import classify_index_error, file_signature

pytestmark = pytest.mark.unit


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "burst.jpg"
    path.write_bytes(b"\xff\xd8\xff")
    return path


@pytest.mark.parametrize(("error", "code"), [
    (OSError("image file is truncated (78 bytes not processed)"), "decode_error"),  # Pillow: no errno
    (SyntaxError("broken PNG file"), "decode_error"),
    (UnidentifiedImageError("cannot identify image file"), "unsupported_format"),
    (RuntimeError("onnxruntime failure"), "processing_error"),
])
def test_a_file_that_cant_be_processed_fails_permanently(photo, error, code):
    failure = classify_index_error(photo, error)
    assert (failure.code, failure.permanent) == (code, True)
    assert failure.detail.startswith(type(error).__name__ + ": ")


@pytest.mark.parametrize("number", [errno.EIO, errno.EACCES, errno.ENOENT])
def test_a_file_that_couldnt_be_reached_is_retried(photo, number):
    failure = classify_index_error(photo, OSError(number, "drive error"))
    assert (failure.code, failure.permanent) == ("unreadable", False)


def test_a_file_that_is_gone_is_retried(tmp_path):
    failure = classify_index_error(tmp_path / "unmounted.jpg", OSError("image file is truncated"))
    assert (failure.code, failure.permanent) == ("unreadable", False)


def test_a_video_failure_is_a_video_error(photo):
    assert classify_index_error(photo, RuntimeError("ffmpeg"), video=True).code == "video_error"


def test_the_signature_changes_when_the_file_does(photo, tmp_path):
    before = file_signature(photo)
    photo.write_bytes(b"\xff\xd8\xff\xd9 repaired")
    assert file_signature(photo) != before
    assert file_signature(tmp_path / "missing.jpg") is None

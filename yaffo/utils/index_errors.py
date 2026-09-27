"""Why a media file couldn't be indexed, and whether trying again could help.

index_photo / index_video return an IndexFailure instead of their result dict
when a file can't be indexed. The index task then decides:

- **permanent** (the file itself can't be decoded or processed): the item is
  marked FAILED with the code, the English detail and the file's signature. File
  sync leaves it alone until the file changes on disk (a new signature) or the
  user asks to retry.
- **not permanent** (the file couldn't be reached: a drive unmounted mid-run, an
  I/O or permission error): the item stays IMPORTED, so the next sync retries it.

Codes are translated at render time (routes/utilities/index_failures.py); the
detail stays English, for Details and the assistant.
"""
import errno
from dataclasses import dataclass
from pathlib import Path

from PIL import UnidentifiedImageError

INDEX_ERROR_UNREADABLE = "unreadable"            # not permanent: the file couldn't be reached
INDEX_ERROR_UNSUPPORTED = "unsupported_format"   # not an image the decoders recognise
INDEX_ERROR_DECODE = "decode_error"              # a damaged or truncated file
INDEX_ERROR_VIDEO = "video_error"                # the video couldn't be processed
INDEX_ERROR_PROCESSING = "processing_error"      # anything else (metadata, face detection...)

# OSError errnos that mean "couldn't reach the file", as opposed to Pillow's
# decode errors, which are OSErrors without an errno.
_UNREACHABLE_ERRNOS = {
    errno.ENOENT, errno.EIO, errno.EACCES, errno.EPERM, errno.ENXIO, errno.ENODEV,
    errno.ETIMEDOUT, errno.ESTALE, errno.EAGAIN,
}

# Longest English detail kept on the media item.
MAX_DETAIL_CHARS = 500


@dataclass(frozen=True)
class IndexFailure:
    code: str
    detail: str       # English, for Details and the assistant
    permanent: bool   # False: retry on the next sync


def classify_index_error(path: Path, error: Exception, *, video: bool = False) -> IndexFailure:
    """Turn an exception from indexing `path` into an IndexFailure."""
    detail = f"{type(error).__name__}: {error}"[:MAX_DETAIL_CHARS]
    if not path.exists() or (isinstance(error, OSError) and error.errno in _UNREACHABLE_ERRNOS):
        return IndexFailure(INDEX_ERROR_UNREADABLE, detail, permanent=False)
    if video:
        return IndexFailure(INDEX_ERROR_VIDEO, detail, permanent=True)
    if isinstance(error, UnidentifiedImageError):
        return IndexFailure(INDEX_ERROR_UNSUPPORTED, detail, permanent=True)
    if isinstance(error, (OSError, SyntaxError, ValueError)):
        # Pillow reports broken image data as errno-less OSErrors ("image file is
        # truncated", "broken data stream"), and some formats as SyntaxError.
        return IndexFailure(INDEX_ERROR_DECODE, detail, permanent=True)
    return IndexFailure(INDEX_ERROR_PROCESSING, detail, permanent=True)


def file_signature(path: Path) -> str | None:
    """Size and modification time: how file sync tells a changed file from the one
    that failed. None when the file can't be stat'ed."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{stat.st_size}:{stat.st_mtime_ns}"

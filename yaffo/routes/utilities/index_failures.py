"""How a media item that couldn't be indexed reads to the user.

The item stores a reason code (utils/index_errors.py); these translate it at
render time. The English detail is shown only under Details.
"""
from dataclasses import dataclass
from pathlib import Path

from flask_babel import gettext
from sqlalchemy.orm import Session

from yaffo.db.models import MediaItem, MEDIA_STATUS_FAILED
from yaffo.utils import index_errors as codes

# Failed files listed on Index Photos before "and N more".
FAILED_LIST_LIMIT = 50


def index_failure_reason(code: str | None) -> str:
    """A short reason, for the Index Photos list."""
    return {
        codes.INDEX_ERROR_DECODE: gettext("Damaged or incomplete file"),
        codes.INDEX_ERROR_UNSUPPORTED: gettext("Unsupported file format"),
        codes.INDEX_ERROR_VIDEO: gettext("The video couldn't be processed"),
    }.get(code or "", gettext("An error occurred while processing it"))


def index_failure_message(code: str | None) -> str:
    """The note on the photo's page."""
    return {
        codes.INDEX_ERROR_DECODE: gettext(
            "This file couldn't be indexed: it appears to be damaged or incomplete."),
        codes.INDEX_ERROR_UNSUPPORTED: gettext(
            "This file couldn't be indexed: its format isn't supported."),
        codes.INDEX_ERROR_VIDEO: gettext("This video couldn't be indexed."),
    }.get(code or "", gettext("This file couldn't be indexed because of an error while processing it."))


@dataclass(frozen=True)
class FailedMediaView:
    id: int
    file_name: str
    folder: str
    reason: str


def failed_media(session: Session, limit: int = FAILED_LIST_LIMIT) -> tuple[list[FailedMediaView], int]:
    """The first `limit` FAILED items (by path) as list rows, and how many there are."""
    query = session.query(MediaItem).filter(MediaItem.status == MEDIA_STATUS_FAILED)
    items = query.order_by(MediaItem.full_file_path).limit(limit).all()
    views = [
        FailedMediaView(
            id=item.id,
            file_name=Path(item.full_file_path).name,
            folder=str(Path(item.full_file_path).parent),
            reason=index_failure_reason(item.index_error),
        )
        for item in items
    ]
    return views, query.count()

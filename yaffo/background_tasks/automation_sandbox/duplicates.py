"""Read a duplicate scan's results from a script, so the assistant can propose
which copy of each group to keep.

A scan (the Remove Duplicates tool, or the duplicate_scan automation) stores its
groups on the find_duplicates Job as absolute file paths. Scripts never see those:
each file comes back as its indexed media item, with the relative path scripts
already use and the facts that help choose a keeper. Files that aren't indexed
can't be acted on by id, so they're only counted.
"""
import json
from typing import Annotated, Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_sandbox.media_dirs import enrich_media_rows
from yaffo.db.models import JOB_STATUS_COMPLETED, AlbumItem, Face, Job, MediaItem, Tag

JOB_NAME = "find_duplicates"
MAX_GROUPS = 500


def _counts(session: Session, column: Any, ids: list[int]) -> dict[int, int]:
    return dict(session.query(column, func.count()).filter(column.in_(ids)).group_by(column).all())


def duplicate_groups(
    session: Session, job_id: str, limit: int = 100,
) -> Annotated[dict, "{groups: [{group_id, items: [...], unindexed}], total_groups}"]:
    """The groups a finished duplicate scan found (up to `limit`, at most 500). Each
    item is {id, media_dir_id, relative_path, width, height, date_taken, favorite,
    faces, tags, albums}; `unindexed` counts copies Yaffo hasn't indexed."""
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    job = session.get(Job, job_id) if isinstance(job_id, str) else None
    if job is None or job.name != JOB_NAME:
        raise ValueError(f"No duplicate scan with id {job_id!r}")
    if job.status != JOB_STATUS_COMPLETED:
        raise ValueError(f"The duplicate scan hasn't finished (status {job.status.lower()})")
    groups = [group for result in job.results for group in json_groups(result.result_data)]
    shown = groups[:min(limit, MAX_GROUPS)]
    paths = [path for group in shown for path in group["paths"]]
    items = {item.full_file_path: item for item in
             session.query(MediaItem).filter(MediaItem.full_file_path.in_(paths)).all()} if paths else {}
    ids = [item.id for item in items.values()]
    faces = _counts(session, Face.media_item_id, ids)
    tags = _counts(session, Tag.media_item_id, ids)
    albums = _counts(session, AlbumItem.media_item_id, ids)
    rows = enrich_media_rows(session, [
        {"id": item.id, "width": item.width, "height": item.height, "date_taken": item.date_taken,
         "favorite": item.favorite, "faces": faces.get(item.id, 0), "tags": tags.get(item.id, 0),
         "albums": albums.get(item.id, 0)}
        for item in items.values()
    ])
    row_by_id = {row["id"]: row for row in rows}
    return {
        "total_groups": len(groups),
        "groups": [
            {"group_id": group["id"],
             "items": [row_by_id[items[path].id] for path in group["paths"] if path in items],
             "unindexed": sum(1 for path in group["paths"] if path not in items)}
            for group in shown
        ],
    }


def json_groups(result_data: str | None) -> list[dict]:
    return json.loads(result_data) if result_data else []


def summarize_duplicate_groups(args: list[Any], session: Session) -> str:
    return "Read a duplicate scan's results"

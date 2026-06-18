import json
from dataclasses import asdict, dataclass

from flask import render_template, Flask, request, jsonify, Response, stream_with_context
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING

from yaffo.utils.file_sync import MediaScan, iter_media_scan, perform_sync
from yaffo.routes.utilities.common import get_media_dirs, get_thumbnail_dir, automations_sidebar_context


# NDJSON records the scan stream emits (one JSON object per line). Named so the page
# and this route agree on one shape; `type` discriminates the two.
@dataclass
class ScanProgress:
    scanned: int
    type: str = "progress"


@dataclass
class ScanComplete:
    total_filesystem: int
    total_imported: int
    total_indexed: int
    unindexed: list[dict]
    orphaned: list[dict]
    type: str = "done"

    @classmethod
    def from_scan(cls, scan: MediaScan) -> "ScanComplete":
        return cls(
            total_filesystem=scan.total_filesystem,
            total_imported=scan.total_imported,
            total_indexed=scan.total_indexed,
            unindexed=scan.unindexed,
            orphaned=scan.orphaned,
        )


@dataclass
class ScanError:
    message: str
    type: str = "error"


def init_index_photos_routes(app: Flask):
    @app.route("/utilities/index-photos", methods=["GET"])
    def utilities_index_photos():
        """Render the page shell immediately. The filesystem scan (slow on large
        libraries) runs in the streaming `scan` endpoint and fills the counters and
        tables live, so the page no longer blocks on it."""
        warnings = []

        media_dirs = get_media_dirs()
        thumbnail_dir = get_thumbnail_dir()

        if not media_dirs or len(media_dirs) == 0:
            warnings.append({
                'type': 'error',
                'message': 'No media directories configured. Please configure media directories in Settings before syncing.'
            })
        else:
            missing_media_dirs = [str(d) for d in media_dirs if not d.exists()]
            if missing_media_dirs:
                warnings.append({
                    'type': 'warning',
                    'message': f'The following media directories do not exist: {", ".join(missing_media_dirs)}'
                })

        if thumbnail_dir is None:
            warnings.append({
                'type': 'error',
                'message': 'No thumbnail directory configured. Please configure thumbnail directory in Settings before syncing.'
            })
        elif not thumbnail_dir.exists():
            warnings.append({
                'type': 'warning',
                'message': f'Thumbnail directory does not exist: {thumbnail_dir}. It will be created automatically during indexing.'
            })

        can_sync = len(media_dirs) > 0 and all(d.exists() for d in media_dirs) and thumbnail_dir is not None
        can_scan = any(d.exists() for d in media_dirs)

        active_jobs = db.session.query(Job).filter(
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING]),
            Job.name.in_(['index_photos', 'import_photos']),
        ).all()

        return render_template(
            "utilities/index_photos.html",
            **automations_sidebar_context(),
            media_dirs=[str(d) for d in media_dirs],
            active_jobs=[job.to_dict_with_view_props() for job in active_jobs],
            warnings=warnings,
            can_sync=can_sync,
            can_scan=can_scan,
            has_active_jobs=len(active_jobs) > 0,
        )

    @app.route("/utilities/index-photos/scan", methods=["GET"])
    def utilities_index_photos_scan():
        """Stream the media-dir scan as NDJSON: `progress` records with the running
        count while it walks, then one `done` record with the totals + the unindexed/
        orphaned lists (which the client holds for the Sync request)."""
        media_dirs = get_media_dirs()
        thumbnail_dir = get_thumbnail_dir()

        def generate():
            try:
                for event in iter_media_scan(db.session, media_dirs, thumbnail_dir):
                    if isinstance(event, MediaScan):
                        yield json.dumps(asdict(ScanComplete.from_scan(event))) + "\n"
                    else:
                        yield json.dumps(asdict(ScanProgress(scanned=event))) + "\n"
            except Exception as e:
                yield json.dumps(asdict(ScanError(message=str(e)))) + "\n"

        # no-store: the scan is live data, so the browser must re-run it every load
        # rather than serving a stale cached result.
        return Response(
            stream_with_context(generate()),
            mimetype="application/x-ndjson",
            headers={"Cache-Control": "no-store"},
        )

    @app.route("/utilities/index-photos/sync", methods=["POST"])
    def utilities_sync_photos():
        data = request.get_json()
        files_to_index = data.get('files_to_index', [])
        files_to_delete = data.get('files_to_delete', [])

        media_dirs = get_media_dirs()
        thumbnail_dir = get_thumbnail_dir()

        if not media_dirs or len(media_dirs) == 0:
            return jsonify({'error': 'No media directories configured'}), 400

        missing_dirs = [str(d) for d in media_dirs if not d.exists()]
        if missing_dirs:
            return jsonify({'error': f'Media directories do not exist: {", ".join(missing_dirs)}'}), 400

        if thumbnail_dir is None:
            return jsonify({'error': 'No thumbnail directory configured'}), 400

        thumbnail_dir.mkdir(parents=True, exist_ok=True)

        jobs = perform_sync(db.session, files_to_index, files_to_delete, thumbnail_dir)
        return jsonify({'job_id': jobs.import_job_id}), 202

from flask import render_template, Flask, request, jsonify
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING

from yaffo.utils.file_sync import perform_sync, scan_media_dirs
from yaffo.routes.utilities.common import get_media_dirs, get_thumbnail_dir


def init_index_photos_routes(app: Flask):
    @app.route("/utilities/index-photos", methods=["GET"])
    def utilities_index_photos():
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

        scan = scan_media_dirs(db.session, media_dirs, thumbnail_dir)

        active_jobs = db.session.query(Job).filter(
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING]),
            Job.name.in_(['index_photos', 'import_photos']),
        ).all()

        return render_template(
            "utilities/index_photos.html",
            unindexed_photos=scan.unindexed,
            orphaned_photos=scan.orphaned,
            total_imported=scan.total_imported,
            total_indexed=scan.total_indexed,
            total_filesystem=scan.total_filesystem,
            media_dirs=[str(d) for d in media_dirs],
            active_jobs=[job.to_dict_with_view_props() for job in active_jobs],
            warnings=warnings,
            can_sync=can_sync
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
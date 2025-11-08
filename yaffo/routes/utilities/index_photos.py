from flask import render_template, Flask, request, jsonify
from yaffo.db import db
from yaffo.db.models import Photo, Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, PHOTO_STATUS_INDEXED, PHOTO_STATUS_SYNCED
from yaffo.common import PHOTO_EXTENSIONS
from yaffo.background_tasks.tasks import index_photo_task, import_photo_task, schedule_job_completion
from pathlib import Path
from itertools import batched
import uuid
import json

from yaffo.utils.index_photos import delete_orphaned_photos
from yaffo.routes.utilities.common import is_system_file, get_media_dirs, get_thumbnail_dir


def init_index_photos_routes(app: Flask):
    @app.route("/utilities/index-photos", methods=["GET"])
    def utilities_index_photos():
        filesystem_photos = []
        orphaned_db_entries = []
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

        db_photos = db.session.query(Photo.id, Photo.full_file_path, Photo.status).all()

        indexed_paths = {photo[1] for photo in db_photos if photo[2] == PHOTO_STATUS_INDEXED or photo[2] == PHOTO_STATUS_SYNCED}

        filesystem_paths = set()
        for media_dir in media_dirs:
            if media_dir.exists():
                for photo_file in media_dir.rglob("*"):
                    if photo_file.is_file() and photo_file.suffix.lower() in PHOTO_EXTENSIONS:
                        if is_system_file(photo_file.name):
                            continue

                        if thumbnail_dir and photo_file.is_relative_to(thumbnail_dir):
                            continue

                        full_path = str(photo_file)
                        filesystem_paths.add(full_path)

                        if full_path not in indexed_paths:
                            filesystem_photos.append({
                                'filename': photo_file.name,
                                'full_path': full_path
                            })

        for photo in db_photos:
            photo_id, full_path, status = photo
            if not Path(full_path).exists():
                orphaned_db_entries.append({
                    'id': photo_id,
                    'full_path': full_path
                })

        filesystem_photos.sort(key=lambda x: x['full_path'])
        orphaned_db_entries.sort(key=lambda x: x['full_path'])

        active_jobs = db.session.query(Job).filter(
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING]),
            Job.name.in_(['index_photos', 'import_photos']),
        ).all()

        return render_template(
            "utilities/index_photos.html",
            unindexed_photos=filesystem_photos,
            orphaned_photos=orphaned_db_entries,
            total_imported=len(db_photos),
            total_indexed=len(indexed_paths),
            total_filesystem=len(filesystem_paths),
            media_dirs=[str(d) for d in media_dirs],
            active_jobs=[job.to_dict() for job in active_jobs],
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

        db_photos = db.session.query(Photo.id, Photo.full_file_path, Photo.status).all()
        db_photos_dict = {photo[1]: photo for photo in db_photos}

        files_to_import = [file_path for file_path in files_to_index if not file_path in db_photos_dict.keys()]

        import_job_id = str(uuid.uuid4())
        import_job = Job(
            id=import_job_id,
            name='import_photos',
            status=JOB_STATUS_PENDING,
            task_count=len(files_to_import),
            message='Imported {totalCount}/{taskCount} photos',
            completed_count=0,
            error_count=0,
            cancelled_count=0,
            job_data=json.dumps({
                'files_to_import': files_to_import
            })
        )
        files_needing_indexing = [file_path for file_path in files_to_index if
                                  not file_path in db_photos_dict.keys() or
                                  db_photos_dict[file_path][2] != PHOTO_STATUS_INDEXED]
        index_job_id = str(uuid.uuid4())
        index_job = Job(
            id=index_job_id,
            name='index_photos',
            status=JOB_STATUS_PENDING,
            task_count=len(files_needing_indexing),
            message='Indexed {totalCount}/{taskCount} photos',
            completed_count=0,
            error_count=0,
            cancelled_count=0,
            job_data=json.dumps({
                'files_to_index': files_needing_indexing
            })
        )
        db.session.add(import_job)
        db.session.add(index_job)
        db.session.commit()

        for batch in batched(files_to_import, 250):
            import_photo_task(import_job_id, list(batch))
        schedule_job_completion(import_job_id)
        for batch in batched(files_needing_indexing, 10):
            index_photo_task(index_job_id, list(batch))
        delete_orphaned_photos(db.session, files_to_delete)
        schedule_job_completion(index_job_id)
        return jsonify({'job_id': import_job_id}), 202
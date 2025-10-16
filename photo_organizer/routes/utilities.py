from flask import render_template, Flask, redirect, url_for, request, jsonify
from photo_organizer.db import db
from photo_organizer.db.models import Photo, Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED, \
    JOB_STATUS_COMPLETED
from photo_organizer.common import MEDIA_DIR, PHOTO_EXTENSIONS, ROOT_DIR
from photo_organizer.background_tasks.tasks import index_photo_task
from pathlib import Path
from itertools import batched
import uuid
import json

from photo_organizer.utils.index_photos import delete_orphaned_photos


def _is_system_file(filename: str) -> bool:
    system_files = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.Spotlight-V100', '.Trashes', '.fseventsd'}
    return filename.startswith('._') or filename in system_files


def init_utilities_routes(app: Flask):
    @app.route("/utilities", methods=["GET"])
    def utilities_index():
        return redirect(url_for('utilities_index_photos'))

    @app.route("/utilities/index-photos", methods=["GET"])
    def utilities_index_photos():
        filesystem_photos = []
        orphaned_db_entries = []

        indexed_photos = db.session.query(Photo.id, Photo.full_file_path, Photo.relative_file_path).all()
        indexed_paths = {photo[1] for photo in indexed_photos}

        filesystem_paths = set()
        if MEDIA_DIR.exists():
            for photo_file in MEDIA_DIR.rglob("*"):
                if photo_file.is_file() and photo_file.suffix.lower() in PHOTO_EXTENSIONS:
                    if _is_system_file(photo_file.name):
                        continue

                    full_path = str(photo_file)
                    filesystem_paths.add(full_path)

                    if full_path not in indexed_paths:
                        filesystem_photos.append({
                            'relative_path': str(photo_file.relative_to(ROOT_DIR)),
                            'filename': photo_file.name,
                            'full_path': full_path
                        })

        for photo in indexed_photos:
            photo_id, full_path, relative_path = photo
            if not Path(full_path).exists():
                orphaned_db_entries.append({
                    'id': photo_id,
                    'relative_path': relative_path,
                    'full_path': full_path
                })

        filesystem_photos.sort(key=lambda x: x['relative_path'])
        orphaned_db_entries.sort(key=lambda x: x['relative_path'])

        active_jobs = db.session.query(Job).filter(
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING])
        ).all()

        return render_template(
            "utilities/index_photos.html",
            unindexed_photos=filesystem_photos,
            orphaned_photos=orphaned_db_entries,
            total_indexed=len(indexed_paths),
            total_filesystem=len(filesystem_paths),
            media_dir=MEDIA_DIR,
            active_jobs=[job.to_dict() for job in active_jobs]
        )

    @app.route("/utilities/index-photos/sync", methods=["POST"])
    def utilities_sync_photos():
        data = request.get_json()
        files_to_index = data.get('files_to_index', [])
        files_to_delete = data.get('files_to_delete', [])

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            name='index_photos',
            status=JOB_STATUS_RUNNING,
            task_count=len(files_to_index),
            message='Indexed {totalCount}/{taskCount} photos',
            completed_count=0,
            error_count=0,
            cancelled_count=0,
            job_data=json.dumps({
                'files_to_index': files_to_index
            })
        )
        db.session.add(job)
        db.session.commit()

        for batch in batched(files_to_index, 10):
            index_photo_task(job_id, list(batch))
        delete_orphaned_photos(db.session, files_to_delete)

        return jsonify({'job_id': job_id}), 202

    @app.route("/utilities/jobs/<job_id>", methods=["GET"])
    def utilities_get_job_status(job_id: str):
        job = db.session.query(Job).filter_by(id=job_id).first()
        if not job:
            return jsonify({'error': 'Job not found'}), 404
        job_completed = job.task_count == (job.completed_count + job.error_count + job.cancelled_count)
        if job_completed:
            job.status = JOB_STATUS_COMPLETED
            db.session.commit()

        return jsonify(job.to_dict())

    @app.route("/utilities/jobs/<job_id>/cancel", methods=["POST"])
    def utilities_cancel_job(job_id: str):
        job = db.session.query(Job).filter_by(id=job_id).first()
        if not job:
            return jsonify({'success': False, 'message': 'Job not found'}), 400

        if job.status in [JOB_STATUS_PENDING, JOB_STATUS_RUNNING]:
            job.status = JOB_STATUS_CANCELLED
            db.session.commit()
            return jsonify({'success': True, 'message': 'Cancellation requested'}), 200
        else:
            return jsonify({'success': False, 'message': 'Job cannot be cancelled'}), 400

    @app.route("/utilities/auto-assign-people", methods=["GET"])
    def utilities_auto_assign_people():
        return render_template("utilities/auto_assign_people.html")

    @app.route("/utilities/discover-people", methods=["GET"])
    def utilities_discover_people():
        return render_template("utilities/discover_people.html")
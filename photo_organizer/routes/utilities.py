from flask import render_template, Flask, redirect, url_for, request, jsonify
from photo_organizer.db import db
from photo_organizer.db.models import Photo
from photo_organizer.common import MEDIA_DIR, PHOTO_EXTENSIONS, ROOT_DIR
from photo_organizer.job_tracker import job_tracker, JobStatus
from photo_organizer.utils.index_photos import index_photos_batch, delete_orphaned_photos
from pathlib import Path
import threading
from datetime import datetime


def _is_system_file(filename: str) -> bool:
    system_files = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.Spotlight-V100', '.Trashes', '.fseventsd'}
    return filename.startswith('._') or filename in system_files


def _run_sync_job(job_id: str, files_to_index: list, files_to_delete: list, app: Flask):
    job = job_tracker.get_job(job_id)
    if not job:
        return

    with app.app_context():
        try:
            job_tracker.update_job(job_id, status=JobStatus.RUNNING, started_at=datetime.now())

            total_operations = len(files_to_index) + len(files_to_delete)
            job_tracker.update_job(job_id, total=total_operations if total_operations > 0 else 100)

            indexed_count = 0
            deleted_count = 0

            if files_to_index:
                if job.cancel_requested:
                    raise InterruptedError("Job cancelled by user")

                job_tracker.update_job(
                    job_id,
                    message=f"Indexing {len(files_to_index)} photos..."
                )

                def progress_callback(current, total):
                    if job.cancel_requested:
                        raise InterruptedError("Job cancelled by user")

                    progress = int((current / total_operations) * 100)
                    job_tracker.update_job(
                        job_id,
                        progress=progress,
                        message=f"Indexing photos: {current}/{len(files_to_index)}"
                    )

                indexed_count, errors = index_photos_batch(
                    db.session,
                    files_to_index,
                    max_workers=8,
                    progress_callback=progress_callback
                )

            if files_to_delete:
                if job.cancel_requested:
                    raise InterruptedError("Job cancelled by user")

                job_tracker.update_job(
                    job_id,
                    message=f"Deleting {len(files_to_delete)} orphaned entries..."
                )

                deleted_count = delete_orphaned_photos(db.session, files_to_delete)

                progress = int(((len(files_to_index) + len(files_to_delete)) / total_operations) * 100)
                job_tracker.update_job(
                    job_id,
                    progress=progress,
                    message=f"Deleted {deleted_count} orphaned entries"
                )

            job_tracker.update_job(
                job_id,
                status=JobStatus.COMPLETED,
                completed_at=datetime.now(),
                progress=100,
                message=f"Sync completed: {indexed_count} indexed, {deleted_count} deleted"
            )
        except InterruptedError as e:
            job_tracker.update_job(
                job_id,
                status=JobStatus.CANCELLED,
                completed_at=datetime.now(),
                message=f"Sync cancelled: {indexed_count} indexed, {deleted_count} deleted before cancellation"
            )
        except Exception as e:
            job_tracker.update_job(
                job_id,
                status=JobStatus.FAILED,
                completed_at=datetime.now(),
                error=str(e),
                message=f"Sync failed: {str(e)}"
            )


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

        active_jobs = [job for job in job_tracker.get_all_jobs()
                       if job.status in [JobStatus.PENDING, JobStatus.RUNNING]]

        return render_template(
            "utilities/index_photos.html",
            unindexed_photos=filesystem_photos,
            orphaned_photos=orphaned_db_entries,
            total_indexed=len(indexed_paths),
            total_filesystem=len(filesystem_paths),
            media_dir=MEDIA_DIR,
            active_jobs=active_jobs
        )

    @app.route("/utilities/index-photos/sync", methods=["POST"])
    def utilities_sync_photos():
        data = request.get_json()
        files_to_index = data.get('files_to_index', [])
        files_to_delete = data.get('files_to_delete', [])

        job = job_tracker.create_job('sync_photos', {
            'files_to_index': files_to_index,
            'files_to_delete': files_to_delete
        })

        thread = threading.Thread(
            target=_run_sync_job,
            args=(job.id, files_to_index, files_to_delete, app)
        )
        thread.daemon = True
        thread.start()

        return jsonify({'job_id': job.id}), 202

    @app.route("/utilities/jobs/<job_id>", methods=["GET"])
    def utilities_get_job_status(job_id: str):
        job = job_tracker.get_job(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404

        return jsonify(job.to_dict())

    @app.route("/utilities/jobs/<job_id>/cancel", methods=["POST"])
    def utilities_cancel_job(job_id: str):
        if job_tracker.cancel_job(job_id):
            return jsonify({'success': True, 'message': 'Cancellation requested'}), 200
        else:
            return jsonify({'success': False, 'message': 'Job not found or cannot be cancelled'}), 400

    @app.route("/utilities/auto-assign-people", methods=["GET"])
    def utilities_auto_assign_people():
        return render_template("utilities/auto_assign_people.html")

    @app.route("/utilities/discover-people", methods=["GET"])
    def utilities_discover_people():
        return render_template("utilities/discover_people.html")
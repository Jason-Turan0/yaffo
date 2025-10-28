from flask import render_template, Flask, redirect, url_for, request, jsonify
from yaffo.db import db
from yaffo.db.models import Photo, Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED, \
    JOB_STATUS_COMPLETED, Person, Face, FACE_STATUS_UNASSIGNED, JobResult, \
    PHOTO_STATUS_INDEXED, PHOTO_STATUS_SYNCED, ApplicationSettings
from yaffo.common import PHOTO_EXTENSIONS
from yaffo.background_tasks.tasks import index_photo_task, auto_assign_faces_task, import_photo_task, sync_metadata_task
from pathlib import Path
from itertools import batched
import uuid
import json

from yaffo.utils.index_photos import delete_orphaned_photos, get_exif_data_with_exiftool
from sqlalchemy.orm import joinedload


def _is_system_file(filename: str) -> bool:
    system_files = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.Spotlight-V100', '.Trashes', '.fseventsd'}
    return filename.startswith('._') or filename in system_files


def _get_media_dirs() -> list[Path]:
    media_dirs_setting = db.session.query(ApplicationSettings).filter_by(name="media_dirs").first()

    if media_dirs_setting and media_dirs_setting.value:
        media_dir_paths = json.loads(media_dirs_setting.value)
        return [Path(dir_path) for dir_path in media_dir_paths]
    else:
        return []


def _get_thumbnail_dir() -> Path | None:
    thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

    if thumbnail_setting and thumbnail_setting.value:
        return Path(thumbnail_setting.value)
    else:
        return None


def init_utilities_routes(app: Flask):
    @app.route("/utilities", methods=["GET"])
    def utilities_index():
        return redirect(url_for('utilities_index_photos'))

    @app.route("/utilities/index-photos", methods=["GET"])
    def utilities_index_photos():
        filesystem_photos = []
        orphaned_db_entries = []
        warnings = []

        media_dirs = _get_media_dirs()
        thumbnail_dir = _get_thumbnail_dir()

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

        indexed_paths = {photo[1] for photo in db_photos if photo[2] == PHOTO_STATUS_INDEXED}

        filesystem_paths = set()
        for media_dir in media_dirs:
            if media_dir.exists():
                for photo_file in media_dir.rglob("*"):
                    if photo_file.is_file() and photo_file.suffix.lower() in PHOTO_EXTENSIONS:
                        if _is_system_file(photo_file.name):
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

        media_dirs = _get_media_dirs()
        thumbnail_dir = _get_thumbnail_dir()

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
        for batch in batched(files_needing_indexing, 10):
            index_photo_task(index_job_id, list(batch))
        delete_orphaned_photos(db.session, files_to_delete)
        return jsonify({'job_id': import_job_id}), 202

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

    @app.route("/utilities/jobs/<job_id>/delete", methods=["POST"])
    def utilities_delete_job(job_id: str):
        job = db.session.query(Job).filter_by(id=job_id).first()
        if not job:
            return "Job not found", 404
        JobResult.query.filter(JobResult.job_id == job_id).delete()
        db.session.delete(job)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Job deleted'}), 200

    @app.route("/utilities/auto-assign", methods=["GET"])
    def utilities_auto_assign():
        people = db.session.query(Person).order_by(Person.name).all()
        unassigned_count = db.session.query(Face).filter(Face.status == FACE_STATUS_UNASSIGNED).count()

        active_jobs = db.session.query(Job).filter(
            Job.name == 'auto_assign_faces',
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED])
        ).all()

        return render_template(
            "utilities/auto_assign.html",
            people=[{"id": person.id, "name": person.name} for person in people],
            unassigned_count=unassigned_count,
            active_jobs=[job.to_dict() for job in active_jobs]
        )

    @app.route("/utilities/auto-assign-people/start", methods=["POST"])
    def utilities_auto_assign_start():
        data = request.get_json()
        person_id = data.get('person_id')
        similarity_threshold = data.get('similarity_threshold', 0.95)

        if not person_id:
            return jsonify({'error': 'Person ID is required'}), 400

        person = db.session.get(Person, person_id)
        if not person:
            return jsonify({'error': 'Person not found'}), 404

        unassigned_faces = db.session.query(Face.id).filter(Face.status == FACE_STATUS_UNASSIGNED).all()
        unassigned_face_ids = [face_id for (face_id,) in unassigned_faces]

        if not unassigned_face_ids:
            return jsonify({'error': 'No unassigned faces to process'}), 400

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            name='auto_assign_faces',
            status=JOB_STATUS_RUNNING,
            task_count=len(unassigned_face_ids),
            message='Processed {totalCount}/{taskCount} faces',
            completed_count=0,
            error_count=0,
            cancelled_count=0,
            job_data=json.dumps({
                'person_id': person_id,
                'person_name': person.name,
                'similarity_threshold': similarity_threshold,
                'unassigned_face_ids': unassigned_face_ids
            })
        )
        db.session.add(job)
        db.session.commit()

        for batch in batched(unassigned_face_ids, 100):
            auto_assign_faces_task(job_id=job_id, face_id_batch=list(batch), person_id=person_id,
                                   similarity_threshold=similarity_threshold)

        return jsonify({'job_id': job_id}), 202

    @app.route("/utilities/sync-metadata", methods=["GET"])
    def utilities_sync_metadata():
        # Only sync photos that are INDEXED (not already SYNCED)
        photos_with_metadata = db.session.query(Photo).filter(
            Photo.status == PHOTO_STATUS_INDEXED or Photo.status == PHOTO_STATUS_SYNCED
        ).filter(
            (Photo.location_name.isnot(None)) | (Photo.faces.any())
        ).all()

        photos_to_sync = []
        for photo in photos_with_metadata:
            photo_path = Path(photo.full_file_path)
            if not photo_path.exists() or photo.status == PHOTO_STATUS_SYNCED:
                continue

            people_names = set()
            for face in photo.faces:
                for person in face.people:
                    if person.name:
                        people_names.add(person.name)

            if photo.location_name or people_names:
                photos_to_sync.append({
                    'photo_id': photo.id,
                    'filename': photo_path.name,
                    'full_path': str(photo_path),
                    'location_name': photo.location_name,
                    'people_names': sorted(list(people_names))
                })

        total_photos = db.session.query(Photo).count()
        synced_photos = db.session.query(Photo).filter(Photo.status == PHOTO_STATUS_SYNCED).count()

        active_jobs = db.session.query(Job).filter(
            Job.name == 'sync_metadata',
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING])
        ).all()

        return render_template(
            "utilities/sync_metadata.html",
            total_photos=total_photos,
            photos_with_metadata=len(photos_with_metadata),
            photos_to_sync=photos_to_sync,
            active_jobs=[job.to_dict() for job in active_jobs]
        )

    @app.route("/utilities/sync-metadata/start", methods=["POST"])
    def utilities_sync_metadata_start():
        data = request.get_json()
        photo_ids = data.get('photo_ids', [])

        if not photo_ids:
            return jsonify({'error': 'No photos specified'}), 400

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            name='sync_metadata',
            status=JOB_STATUS_PENDING,
            task_count=len(photo_ids),
            message='Synced {totalCount}/{taskCount} photos',
            completed_count=0,
            error_count=0,
            cancelled_count=0,
            job_data=json.dumps({
                'photo_ids': photo_ids
            })
        )
        db.session.add(job)
        db.session.commit()

        for batch in batched(photo_ids, 50):
            sync_metadata_task(job_id=job_id, photo_id_batch=list(batch))

        return jsonify({'job_id': job_id}), 202

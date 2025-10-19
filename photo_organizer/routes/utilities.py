from flask import render_template, Flask, redirect, url_for, request, jsonify
from photo_organizer.db import db
from photo_organizer.db.models import Photo, Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED, \
    JOB_STATUS_COMPLETED, Person, Face, FACE_STATUS_UNASSIGNED, PersonFace, FACE_STATUS_ASSIGNED, JobResult, \
    PHOTO_STATUS_INDEXED, PHOTO_STATUS_IMPORTED
from photo_organizer.common import MEDIA_DIR, PHOTO_EXTENSIONS, ROOT_DIR
from photo_organizer.background_tasks.tasks import index_photo_task, auto_assign_faces_task, import_photo_task
from pathlib import Path
from itertools import batched
import uuid
import json

from photo_organizer.utils.index_photos import delete_orphaned_photos
from photo_organizer.db.repositories.person_repository import update_person_embedding
from sqlalchemy.orm import joinedload


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

        db_photos = db.session.query(Photo.id, Photo.full_file_path, Photo.relative_file_path, Photo.status).all()

        indexed_paths = {photo[1] for photo in db_photos if photo[3] == PHOTO_STATUS_INDEXED}

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

        for photo in db_photos:
            photo_id, full_path, relative_path, status = photo
            if not Path(full_path).exists():
                orphaned_db_entries.append({
                    'id': photo_id,
                    'relative_path': relative_path,
                    'full_path': full_path
                })

        filesystem_photos.sort(key=lambda x: x['relative_path'])
        orphaned_db_entries.sort(key=lambda x: x['relative_path'])

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
            media_dir=MEDIA_DIR,
            active_jobs=[job.to_dict() for job in active_jobs]
        )

    @app.route("/utilities/index-photos/sync", methods=["POST"])
    def utilities_sync_photos():
        data = request.get_json()
        files_to_index = data.get('files_to_index', [])
        files_to_delete = data.get('files_to_delete', [])

        import_job_id = str(uuid.uuid4())
        import_job = Job(
            id=import_job_id,
            name='import_photos',
            status=JOB_STATUS_PENDING,
            task_count=len(files_to_index),
            message='Imported {totalCount}/{taskCount} photos',
            completed_count=0,
            error_count=0,
            cancelled_count=0,
            job_data=json.dumps({
                'files_to_import': files_to_index
            })
        )
        index_job_id = str(uuid.uuid4())
        index_job = Job(
            id=index_job_id,
            name='index_photos',
            status=JOB_STATUS_PENDING,
            task_count=len(files_to_index),
            message='Indexed {totalCount}/{taskCount} photos',
            completed_count=0,
            error_count=0,
            cancelled_count=0,
            job_data=json.dumps({
                'files_to_index': files_to_index
            })
        )
        db.session.add(import_job)
        db.session.add(index_job)
        db.session.commit()

        for batch in batched(files_to_index, 25):
            import_photo_task(import_job_id, list(batch))
        for batch in batched(files_to_index, 10):
            index_photo_task(import_job_id, list(batch))
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
            auto_assign_faces_task(job_id=job_id, face_id_batch = list(batch), person_id = person_id, similarity_threshold =similarity_threshold)

        return jsonify({'job_id': job_id}), 202

    @app.route("/utilities/auto-assign-people/results/<job_id>", methods=["GET"])
    def utilities_auto_assign_results(job_id: str):
        job = db.session.query(Job).options(joinedload(Job.results)).filter_by(id=job_id).first()
        if not job:
            return "Job not found", 404

        if job.status != JOB_STATUS_COMPLETED:
            return redirect(url_for('utilities_auto_assign_people'))

        job_data = json.loads(job.job_data) if job.job_data else {}

        matched_faces = []
        for result in job.results:
            data= json.loads(result.result_data)
            matched_faces.extend(data["matches"])

        person_id = job_data.get('person_id')
        person_name = job_data.get('person_name')
        similarity_threshold = job_data.get('similarity_threshold')
        face_ids = [match['face_id'] for match in matched_faces]
        faces = (
            db.session.query(Face)
            .filter(Face.id.in_(face_ids))
            .options(joinedload(Face.photo))
            .all()
        ) if len(face_ids) > 0 else []
        face_dict = {face.id: face for face in faces}
        faces_with_similarity = [
            {
                'face': face_dict[match['face_id']],
                'similarity': match['similarity']
            }
            for match in matched_faces
            if match['face_id'] in face_dict
        ]
        faces_with_similarity.sort(key=lambda face: face['similarity'])
        return render_template(
            "utilities/auto_assign_results.html",
            job_id=job_id,
            person_id=person_id,
            person_name=person_name,
            similarity_threshold=similarity_threshold,
            faces=faces_with_similarity,
            total_matches=len(matched_faces)
        )

    @app.route("/utilities/discover-people", methods=["GET"])
    def utilities_discover_people():
        return render_template("utilities/discover_people.html")
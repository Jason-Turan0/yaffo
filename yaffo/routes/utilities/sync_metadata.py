from flask import render_template, Flask, request, jsonify
from yaffo.db import db
from yaffo.db.models import Photo, Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, PHOTO_STATUS_INDEXED, PHOTO_STATUS_SYNCED
from yaffo.background_tasks.tasks import sync_metadata_task
from pathlib import Path
from itertools import batched
import uuid
import json


def init_sync_metadata_routes(app: Flask):
    @app.route("/utilities/sync-metadata", methods=["GET"])
    def utilities_sync_metadata():
        photos_with_metadata = db.session.query(Photo).filter(
            (Photo.status == PHOTO_STATUS_INDEXED) | (Photo.status == PHOTO_STATUS_SYNCED)
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
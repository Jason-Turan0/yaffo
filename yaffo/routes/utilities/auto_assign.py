from flask import render_template, Flask, redirect, url_for, request, jsonify
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED, Person, Face, FACE_STATUS_UNASSIGNED
from yaffo.background_tasks.tasks import auto_assign_faces_task, schedule_job_completion
from itertools import batched
import uuid
import json

from sqlalchemy.orm import joinedload


def init_auto_assign_routes(app: Flask):
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

        schedule_job_completion(job_id)

        return jsonify({'job_id': job_id}), 202

    @app.route("/utilities/auto-assign-people/results/<job_id>", methods=["GET"])
    def utilities_auto_assign_results(job_id: str):
        job = db.session.query(Job).options(joinedload(Job.results)).filter_by(id=job_id).first()
        if not job:
            return "Job not found", 404

        if job.status != JOB_STATUS_COMPLETED:
            return redirect(url_for('utilities_auto_assign'))

        job_data = json.loads(job.job_data) if job.job_data else {}

        matched_faces = []
        for result in job.results:
            data = json.loads(result.result_data)
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
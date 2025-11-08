from flask import Flask, redirect, url_for, request, jsonify, render_template
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_CANCELLED, JOB_STATUS_COMPLETED, JobResult

def init_base_utilities_routes(app: Flask):
    @app.route("/utilities", methods=["GET"])
    def utilities_index():
        return redirect(url_for('utilities_index_photos'))

    @app.route("/utilities/jobs-section", methods=["GET"])
    def utilities_jobs_section():
        job_name = request.args.get('job_name')
        has_results = request.args.get('has_results', 'false').lower() == 'true'

        if not job_name:
            return "Job name is required", 400

        active_jobs = db.session.query(Job).filter(
            Job.name == job_name,
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED])
        ).all()

        return render_template(
            "fragments/job_section_fragment.html",
            active_jobs=active_jobs,
            show_cancel=True,
            has_results=has_results
        )

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

        job_name = job.name

        JobResult.query.filter(JobResult.job_id == job_id).delete()
        db.session.delete(job)
        db.session.commit()

        remaining_jobs = db.session.query(Job).filter(
            Job.name == job_name,
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED])
        ).count()

        if remaining_jobs == 0:
            return '<div id="job-progress-section" hx-swap-oob="delete"></div>', 200

        return '', 200
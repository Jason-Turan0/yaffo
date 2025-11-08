from flask import render_template, Flask, jsonify, request, make_response
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JobResult
import json


def init_jobs_routes(app: Flask):
    @app.route("/jobs/<job_id>/fragment", methods=["GET"])
    def job_fragment(job_id: str):
        """Returns HTML fragment for a single job card - used by htmx polling"""
        job = db.session.query(Job).filter_by(id=job_id).first()
        if not job:
            return "", 404

        # Calculate progress
        total_count = job.completed_count + job.error_count + job.cancelled_count
        progress = (total_count / job.task_count * 100) if job.task_count > 0 else 0

        # Determine if job is finished (stop polling)
        is_finished = job.status in [JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED]

        # Get has_results from query parameter (defaults to False)
        has_results = request.args.get('has_results', '0') == '1'

        return render_template(
            "fragments/job_status_fragment.html",
            job=job,
            progress=progress,
            total_count=total_count,
            is_finished=is_finished,
            has_results=has_results,
            show_cancel=True
        )

    @app.route("/jobs/<job_id>/cancel", methods=["POST"])
    def job_cancel(job_id: str):
        """Cancel a running job - returns updated fragment"""
        job = db.session.query(Job).filter_by(id=job_id).first()
        if not job:
            return "", 404

        if job.status in [JOB_STATUS_PENDING, JOB_STATUS_RUNNING]:
            job.status = JOB_STATUS_CANCELLED
            db.session.commit()

            # Return updated fragment
            total_count = job.completed_count + job.error_count + job.cancelled_count
            progress = (total_count / job.task_count * 100) if job.task_count > 0 else 0
            is_finished = True

            # Get has_results from request (htmx sends it via hx-vals)
            has_results = request.form.get('has_results', 'false').lower() == 'true'

            return render_template(
                "fragments/job_status_fragment.html",
                job=job,
                progress=progress,
                total_count=total_count,
                is_finished=is_finished,
                has_results=has_results,
                show_cancel=True
            )

        return "", 400

    @app.route("/jobs/<job_id>/delete", methods=["POST"])
    def job_delete(job_id: str):
        """Delete a finished job - returns empty to remove element"""
        job = db.session.query(Job).filter_by(id=job_id).first()
        if not job:
            return "", 404

        JobResult.query.filter(JobResult.job_id == job_id).delete()
        db.session.delete(job)
        db.session.commit()

        # Return empty to remove element from DOM with notification trigger
        response = make_response("", 200)
        response.headers['HX-Trigger'] = json.dumps({
            'showNotification': {
                'message': 'Job deleted',
                'type': 'success'
            }
        })
        return response
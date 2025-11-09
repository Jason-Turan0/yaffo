from flask import render_template, Flask, request, jsonify
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED
from yaffo.common import PHOTO_EXTENSIONS
from yaffo.background_tasks.tasks import find_duplicates_task
from pathlib import Path
import uuid
import json

from yaffo.routes.utilities.common import is_system_file, get_thumbnail_dir
from yaffo.utils.file_system import show_file_dialog


def collect_photo_paths(directory_paths: list[str]) -> list[str]:
    found_paths = set()
    thumbnail_dir = get_thumbnail_dir()

    for directory_path in directory_paths:
        dir_path = Path(directory_path)
        if not dir_path.exists() or not dir_path.is_dir() or dir_path == '':
            continue
        for p in dir_path.rglob("*"):
            if not (p.suffix.lower() in PHOTO_EXTENSIONS and not p.name.startswith(".") and p.is_file()):
                continue
            if thumbnail_dir and p.is_relative_to(thumbnail_dir):
                continue
            if is_system_file(p.name):
                continue
            if str(p) in found_paths:
                continue
            found_paths.add(str(p))

    return list(found_paths)


def count_photos_in_directory(directory_paths: list[str]) -> int:
    return len(collect_photo_paths(directory_paths))

def init_remove_duplicates_routes(app: Flask):
    @app.route("/utilities/remove-duplicates", methods=["GET"])
    def utilities_remove_duplicates():
        active_jobs = db.session.query(Job).filter(
            Job.name == 'remove_duplicates',
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED])
        ).all()

        return render_template(
            "utilities/remove_duplicates.html",
            active_jobs=[job.to_dict() for job in active_jobs],
            directories=[],
            total_photos=0
        )

    @app.route("/utilities/remove-duplicates-form", methods=["POST"])
    def utilities_remove_duplicates_form():
        directories = request.form.getlist("directory")
        action = request.form.get("action")
        index = request.form.get("index", type=int)

        if action == "create":
            selected_folder = show_file_dialog()
            if selected_folder.success and selected_folder.selected_path is not None:
                directories.append(selected_folder.selected_path)
            else:
                directories.append('')

        if action == "remove":
            directories.remove(directories[index])

        if action == "browse":
            selected_folder = show_file_dialog()
            if selected_folder.success and selected_folder.selected_path is not None:
                directories[index] = selected_folder.selected_path

        total_photos = count_photos_in_directory(directories)

        return render_template(
            "utilities/remove_duplicates_form.html",
            total_photos=total_photos,
            directories=directories
        )

    @app.route("/utilities/remove-duplicates/start", methods=["POST"])
    def utilities_remove_duplicates_start():
        directories = request.form.getlist("directory")
        directories = [d.strip() for d in directories if d.strip()]

        if not directories:
            return jsonify({'error': 'At least one directory is required'}), 400

        file_paths = collect_photo_paths(directories)

        if not file_paths:
            return jsonify({'error': 'No photo files found in selected directories'}), 400

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            name='remove_duplicates',
            status=JOB_STATUS_PENDING,
            task_count=len(file_paths),
            message='Processed {totalCount}/{taskCount} photos',
            completed_count=0,
            error_count=0,
            cancelled_count=0,
            job_data=json.dumps({
                'directories': directories,
                'total_files': len(file_paths)
            })
        )
        db.session.add(job)
        db.session.commit()

        find_duplicates_task(job_id=job_id, file_paths=file_paths)

        return jsonify({'job_id': job_id}), 202

    @app.route("/utilities/remove-duplicates/results/<job_id>", methods=["GET"])
    def utilities_remove_duplicates_results(job_id: str):
        job = db.session.query(Job).filter_by(id=job_id).first()
        if not job:
            return "Job not found", 404

        if job.status != JOB_STATUS_COMPLETED:
            return "Job not completed yet", 400

        return render_template(
            "utilities/remove_duplicates_results.html",
            job=job.to_dict()
        )



from flask import render_template, Flask, request, jsonify
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING
from yaffo.common import PHOTO_EXTENSIONS
from pathlib import Path

from yaffo.routes.utilities.common import is_system_file, get_thumbnail_dir
from yaffo.utils.file_system import show_file_dialog


def count_photos_in_directory(directory_paths: list[str]) -> int:
    found_paths = set()
    thumbnail_dir = get_thumbnail_dir()
    photo_count = 0
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
            photo_count += 1

    return photo_count

def init_remove_duplicates_routes(app: Flask):
    @app.route("/utilities/remove-duplicates", methods=["GET"])
    def utilities_remove_duplicates():
        active_jobs = db.session.query(Job).filter(
            Job.name == 'remove_duplicates',
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING])
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



from flask import render_template, Flask, request, jsonify, redirect, url_for
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED
from yaffo.common import PHOTO_EXTENSIONS
from yaffo.background_tasks.tasks import find_duplicates_task
from pathlib import Path
from sqlalchemy.orm import joinedload
import uuid
import json
import send2trash
import os

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

    def create_duplicate_job_state(job_id: str):
        job = db.session.query(Job).options(joinedload(Job.results)).filter_by(id=job_id).first()
        if not job:
            return "Job not found", 404

        duplicate_groups = []
        selected_files = 0
        current_path_id = 0
        for result in job.results:
            groups = json.loads(result.result_data)
            for group in groups:
                view_group = {
                    'id': group['id'],
                    'paths': []
                }
                for index, path in enumerate(group['paths']):
                    current_path_id += 1
                    view_group['paths'].append({
                        'pathId': current_path_id,
                        'path': path,
                        'selected': index != 0
                    })
                    selected_files += 1 if index != 0 else 0
                duplicate_groups.append(view_group)

        total_groups = len(duplicate_groups)
        total_duplicate_files = sum(len(group['paths']) for group in duplicate_groups)

    @app.route("/utilities/remove-duplicates/results/<job_id>", methods=["GET"])
    def utilities_remove_duplicates_results(job_id: str):
        job = db.session.query(Job).options(joinedload(Job.results)).filter_by(id=job_id).first()
        if not job:
            return "Job not found", 404

        duplicate_groups = []
        selected_files = 0
        current_path_id = 0
        for result in job.results:
            groups = json.loads(result.result_data)
            for group in groups:
                view_group = {
                    'id': group['id'],
                    'paths': []
                }
                for index, path in enumerate(group['paths']):
                    current_path_id += 1
                    view_group['paths'].append({
                        'pathId': current_path_id,
                        'path': path,
                        'selected': index != 0
                    })
                    selected_files += 1 if index != 0 else 0
                duplicate_groups.append(view_group)

        total_groups = len(duplicate_groups)
        total_duplicate_files = sum(len(group['paths']) for group in duplicate_groups)

        return render_template(
            "utilities/remove_duplicates_results.html",
            duplicate_groups=duplicate_groups[:10],
            total_files_processed=job.task_count,
            total_groups=total_groups,
            total_duplicate_files=total_duplicate_files,
            selected_count=selected_files,
            pagination={
                "current_page": 1,
                "total_items": len(duplicate_groups),
                "page_size": 10,
                "page_sizes": [5, 10, 20, 50],
            }
        )

    @app.route("/utilities/remove-duplicates/results-form/<job_id>", methods=["POST"])
    def utilities_remove_duplicates_results_form(job_id: str):
        job = db.session.query(Job).options(joinedload(Job.results)).filter_by(id=job_id).first()
        if not job:
            return "Job not found", 404

        if job.status != JOB_STATUS_COMPLETED:
            return "Job not completed", 400

        page = request.form.get('page', 1, type=int)
        page_size = request.form.get('page_size', 10, type=int)

        group_ids = request.form.getlist('group_id')
        paths = request.form.getlist('path')
        pathIds = request.form.getlist('pathId', type=int)
        selected_flags = request.form.getlist('selected')

        photo_states = {}
        for i in range(len(paths)):
            if i < len(paths):
                photo_states[paths[i]] = {
                    'group_id': int(group_ids[i]) if i < len(group_ids) else 0,
                    'selected': selected_flags[i] == 'true' if i < len(selected_flags) else False
                }

        duplicate_groups = []
        for result in job.results:
            groups = json.loads(result.result_data)
            view_groups = [{
                'id': group['id'],
                'paths': [{
                    'path': path,
                    'selected': photo_states.get(path, {}).get('selected', False)
                } for path in group['paths']]
            } for group in groups]
            duplicate_groups.extend(view_groups)

        total_groups = len(duplicate_groups)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paged_groups = duplicate_groups[start_idx:end_idx]

        selected_count = sum(1 for group in duplicate_groups for path in group['paths'] if path.get('selected', False))

        return render_template(
            "utilities/remove_duplicates_results_form.html",
            duplicate_groups=paged_groups,
            all_duplicate_groups=duplicate_groups,
            job_id=job_id,
            total_files_processed=job.task_count,
            total_groups=total_groups,
            selected_count=selected_count,
            pagination={
                "current_page": page,
                "total_items": total_groups,
                "page_size": page_size,
                "page_sizes": [5, 10, 20, 50],
            }
        )

    @app.route("/utilities/remove-duplicates/toggle-photo", methods=["POST"])
    def utilities_remove_duplicates_toggle_photo():
        target_group_id = request.form.get('target_group_id', type=int)
        target_path_id = request.form.get('target_path_id', type=int)

        group_ids = request.form.getlist('group_id')
        paths = request.form.getlist('path')
        pathIds = request.form.getlist('pathId', type=int)
        selected_flags = request.form.getlist('selected')
        update_index = pathIds.index(target_path_id)
        if update_index == -1:
            raise Exception('Target path id not found')

        path_obj = {
            'path': paths[update_index],
            'selected': not selected_flags[update_index],
            'pathId': pathIds[update_index],
        }
        group_obj = {'id': target_group_id}

        return render_template(
            "utilities/remove_duplicates_photo_card.html",
            path=path_obj,
            group=group_obj,
        )

    @app.route("/utilities/remove-duplicates/execute/<job_id>", methods=["POST"])
    def utilities_remove_duplicates_execute(job_id: str):
        selected_files = request.form.getlist('selected_files')
        action_type = request.form.get('action_type', 'trash')

        if not selected_files:
            return jsonify({'error': 'No files selected'}), 400

        success_count = 0
        error_count = 0
        errors = []

        for file_path in selected_files:
            try:
                file_path_obj = Path(file_path)
                if not file_path_obj.exists():
                    errors.append(f"File not found: {file_path}")
                    error_count += 1
                    continue

                if action_type == 'trash':
                    send2trash.send2trash(str(file_path_obj))
                else:
                    os.remove(str(file_path_obj))

                success_count += 1
            except Exception as e:
                errors.append(f"Error removing {file_path}: {str(e)}")
                error_count += 1

        if success_count > 0:
            message = f"Successfully removed {success_count} file(s)"
            if error_count > 0:
                message += f" ({error_count} errors)"
            return jsonify({'success': True, 'message': message, 'errors': errors}), 200
        else:
            return jsonify({'error': 'Failed to remove files', 'errors': errors}), 500

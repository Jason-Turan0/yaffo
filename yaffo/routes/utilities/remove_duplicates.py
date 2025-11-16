from dataclasses import dataclass

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
import shutil

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


@dataclass
class Pagination:
    current_page: int
    total_items: int
    page_size: int
    page_sizes: list[int]


@dataclass
class PathViewModel:
    path: str
    path_id: int


@dataclass
class DuplicateGroupViewModel:
    group_id: str
    paths: list[PathViewModel]


@dataclass
class DuplicateJobViewModel:
    job_id: str
    processed_photo_count: int
    duplicate_group_count: int
    duplicate_photo_count: int
    duplicates_selected_count: int
    selected_photos: set[int]
    group_page: list[DuplicateGroupViewModel]
    pagination: Pagination


def create_duplicate_job_view_model(job_id: str, page: int, page_size: int):
    job = db.session.query(Job).options(joinedload(Job.results)).filter_by(id=job_id).first()
    if not job:
        return "Job not found", 404

    duplicate_groups: list[DuplicateGroupViewModel] = []
    current_path_id = 0
    for result in job.results:
        groups = json.loads(result.result_data)
        for group in groups:
            paths: list[PathViewModel] = []
            view_group = DuplicateGroupViewModel(
                group_id=group['id'],
                paths=paths
            )
            for index, path in enumerate(group['paths']):
                current_path_id += 1
                paths.append(PathViewModel(
                    path=path,
                    path_id=current_path_id,

                ))
            duplicate_groups.append(view_group)

    selected_photos = set(
        photo.path_id
        for grp in duplicate_groups
        for photo_index, photo in enumerate(grp.paths)
        if photo_index != 0
    )

    total_groups = len(duplicate_groups)
    total_duplicate_files = sum(len(group.paths) for group in duplicate_groups)
    return DuplicateJobViewModel(
        job_id=job_id,
        processed_photo_count=job.task_count,
        duplicate_group_count=len(duplicate_groups),
        duplicate_photo_count=total_duplicate_files,
        duplicates_selected_count= len(selected_photos),
        selected_photos=selected_photos,
        group_page=duplicate_groups[(page * page_size): ((page + 1) * page_size)],
        pagination=Pagination(
            current_page=page,
            total_items=total_groups,
            page_size=page_size,
            page_sizes=[5, 10, 25, 50, 100],
        )
    )


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
        view_model = create_duplicate_job_view_model(job_id=job_id, page=0, page_size=10)
        return render_template(
            "utilities/remove_duplicates_results.html",
            view_model=view_model,
            job_id=job_id,
            action_type='trash',
            destination_folder='',
        )

    @app.route("/utilities/remove-duplicates/results-form/<job_id>", methods=["POST"])
    def utilities_remove_duplicates_results_form(job_id: str):
        page = request.form.get('page', 0, type=int)
        page_size = request.form.get('page_size', 10, type=int)
        action_type = request.form.get('action_type', 'trash')
        destination_folder = request.form.get('destination_folder', '')
        view_model = create_duplicate_job_view_model(job_id=job_id, page=page, page_size=page_size)
        return render_template(
            "utilities/remove_duplicates_results_form.html",
            view_model=view_model,
            job_id=job_id,
            action_type=action_type,
            destination_folder=destination_folder,
        )

    @app.route("/utilities/remove-duplicates/action-change/<job_id>", methods=["POST"])
    def utilities_remove_duplicates_action_change(job_id: str):
        selected_photos: set[int] = set(request.form.getlist('selected_photo', type=int))
        action_type = request.form.get('action_type', 'trash')
        destination_folder = request.form.get('destination_folder', '')
        action = request.form.get('action')

        # Handle browse button click
        if action == 'browse':
            selected_folder = show_file_dialog()
            if selected_folder.success and selected_folder.selected_path is not None:
                destination_folder = selected_folder.selected_path

        # Build view model for rendering
        view_model = type('ViewModel', (), {
            'selected_photos': selected_photos,
            'duplicates_selected_count': len(selected_photos),
            'processed_photo_count': request.form.get('processed_photo_count', type=int, default=0),
            'duplicate_group_count': request.form.get('duplicate_group_count', type=int, default=0),
            'duplicate_photo_count': request.form.get('duplicate_photo_count', type=int, default=0),
        })()

        # Render header with updated action type and destination folder
        return render_template(
            "utilities/remove_duplicates_results_form_header.html",
            view_model=view_model,
            job_id=job_id,
            action_type=action_type,
            destination_folder=destination_folder,
            hx_swap_oob=False
        )

    @app.route("/utilities/remove-duplicates/toggle-photo", methods=["POST"])
    def utilities_remove_duplicates_toggle_photo():
        job_id = request.form.get('job_id')
        target_path_id = request.form.get('target_path_id', type=int)
        target_path = request.form.get('target_path')
        selected_photos: set[int] = set(request.form.getlist('selected_photo', type=int))
        selected_photos ^= {target_path_id}

        # Build view model for rendering
        view_model = type('ViewModel', (), {
            'selected_photos': selected_photos,
            'duplicates_selected_count': len(selected_photos),
            'processed_photo_count': request.form.get('processed_photo_count', type=int, default=0),
            'duplicate_group_count': request.form.get('duplicate_group_count', type=int, default=0),
            'duplicate_photo_count': request.form.get('duplicate_photo_count', type=int, default=0),
        })()

        # Render photo card (main response)
        photo_card = render_template(
            "utilities/remove_duplicates_photo_card.html",
            path={"path": target_path, "path_id": target_path_id},
            view_model=view_model,
        )

        # Render header (OOB update) with current action state
        action_type = request.form.get('action_type', 'trash')
        destination_folder = request.form.get('destination_folder', '')
        header = render_template(
            "utilities/remove_duplicates_results_form_header.html",
            view_model=view_model,
            job_id=job_id,
            action_type=action_type,
            destination_folder=destination_folder,
            hx_swap_oob=True
        )
        return photo_card + header

    @app.route("/utilities/remove-duplicates/execute/<job_id>", methods=["POST"])
    def utilities_remove_duplicates_execute(job_id: str):
        selected_photo_ids: set[int] = set(request.form.getlist('selected_photo', type=int))
        action_type = request.form.get('action_type', 'trash')
        destination_folder = request.form.get('destination_folder', '')

        if not selected_photo_ids:
            response = jsonify({'error': 'No files selected'})
            response.status_code = 400
            response.headers['HX-Trigger'] = json.dumps({
                'showNotification': {'message': 'No files selected', 'type': 'error'}
            })
            return response

        if action_type == 'moveFolder' and not destination_folder:
            response = jsonify({'error': 'Destination folder is required'})
            response.status_code = 400
            response.headers['HX-Trigger'] = json.dumps({
                'showNotification': {'message': 'Please select a destination folder', 'type': 'error'}
            })
            return response

        # Rebuild view model to map photo IDs to file paths
        view_model = create_duplicate_job_view_model(job_id=job_id, page=0, page_size=999999)
        path_map: dict[int, str] = {}
        for group in view_model.group_page:
            for path_vm in group.paths:
                path_map[path_vm.path_id] = path_vm.path

        # Get selected file paths
        selected_files = [path_map[pid] for pid in selected_photo_ids if pid in path_map]

        if not selected_files:
            response = jsonify({'error': 'No valid files found'})
            response.status_code = 400
            response.headers['HX-Trigger'] = json.dumps({
                'showNotification': {'message': 'No valid files found', 'type': 'error'}
            })
            return response

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
                elif action_type == 'delete':
                    os.remove(str(file_path_obj))
                elif action_type == 'moveFolder':
                    dest_dir = Path(destination_folder)
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest_path = dest_dir / file_path_obj.name
                    # Handle duplicate names in destination
                    if dest_path.exists():
                        base = dest_path.stem
                        ext = dest_path.suffix
                        counter = 1
                        while dest_path.exists():
                            dest_path = dest_dir / f"{base}_{counter}{ext}"
                            counter += 1
                    shutil.move(str(file_path_obj), str(dest_path))

                success_count += 1
            except Exception as e:
                errors.append(f"Error processing {file_path}: {str(e)}")
                error_count += 1

        # Build success message
        if success_count > 0:
            if action_type == 'trash':
                message = f"Successfully moved {success_count} file(s) to trash"
            elif action_type == 'delete':
                message = f"Successfully deleted {success_count} file(s)"
            elif action_type == 'moveFolder':
                message = f"Successfully moved {success_count} file(s) to {destination_folder}"

            if error_count > 0:
                message += f" ({error_count} error(s))"

            # Delete the job after successful execution
            job = db.session.query(Job).filter_by(id=job_id).first()
            if job:
                db.session.delete(job)
                db.session.commit()

            response = jsonify({'success': True, 'message': message, 'processed': success_count, 'errors': errors})
            response.headers['HX-Trigger'] = json.dumps({
                'showNotification': {'message': message, 'type': 'success'}
            })
            response.headers['HX-Redirect'] = url_for('utilities_remove_duplicates')
            return response
        else:
            response = jsonify({'error': 'Failed to process files', 'errors': errors})
            response.status_code = 500
            response.headers['HX-Trigger'] = json.dumps({
                'showNotification': {'message': f'Failed to process files: {errors[0] if errors else "Unknown error"}', 'type': 'error'}
            })
            return response

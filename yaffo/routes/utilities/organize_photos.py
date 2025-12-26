from flask import render_template, Flask, request, jsonify
from yaffo.db import db
from yaffo.db.models import Job, JOB_STATUS_PENDING, JOB_STATUS_RUNNING
from yaffo.common import PHOTO_EXTENSIONS
from yaffo.background_tasks.tasks import organize_photos_task, schedule_job_completion
from pathlib import Path
from itertools import batched
import uuid
import json

from yaffo.routes.utilities.common import is_system_file, get_thumbnail_dir


def init_organize_photos_routes(app: Flask):
    @app.route("/utilities/organize-photos", methods=["GET"])
    def utilities_organize_photos():
        active_jobs = db.session.query(Job).filter(
            Job.name == 'organize_photos',
            Job.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING])
        ).all()

        return render_template(
            "utilities/organize_photos.html",
            active_jobs=[job.to_dict_with_view_props() for job in active_jobs]
        )

    @app.route("/utilities/organize-photos/preview", methods=["POST"])
    def utilities_organize_photos_preview():
        from yaffo.utils.photo_dates import get_photo_date
        from calendar import month_name

        data = request.get_json()
        source_directory = data.get('source_directory')
        destination_directory = data.get('destination_directory')
        pattern = data.get('pattern')
        keep_original = data.get('keep_original', False)

        if not source_directory:
            return jsonify({'error': 'Source directory is required'}), 400

        source_path = Path(source_directory)
        if not source_path.exists():
            return jsonify({'error': 'Source directory does not exist'}), 400

        if not source_path.is_dir():
            return jsonify({'error': 'Source path is not a directory'}), 400

        if destination_directory:
            target_path = Path(destination_directory)
            if not target_path.exists():
                return jsonify({'error': 'Destination directory does not exist'}), 400
            if not target_path.is_dir():
                return jsonify({'error': 'Destination path is not a directory'}), 400
        else:
            target_path = source_path

        thumbnail_dir = get_thumbnail_dir()

        photo_files = []
        for p in source_path.rglob("*"):
            if not (p.suffix.lower() in PHOTO_EXTENSIONS and not p.name.startswith(".") and p.is_file()):
                continue

            if thumbnail_dir and p.is_relative_to(thumbnail_dir):
                continue

            if is_system_file(p.name):
                continue

            photo_files.append(p)

        total_files = len(photo_files)
        files_to_move = 0
        files_staying = 0
        file_list = []

        for photo_file in photo_files:
            date_taken = get_photo_date(str(photo_file), exif_data)

            if date_taken:
                if pattern == 'year_month':
                    dest_folder = target_path / str(date_taken.year) / month_name[date_taken.month]
                elif pattern == 'year_month_day':
                    dest_folder = target_path / str(date_taken.year) / month_name[date_taken.month] / f"{date_taken.day:02d}"
                elif pattern == 'year':
                    dest_folder = target_path / str(date_taken.year)
                else:
                    dest_folder = target_path / "unknown"
            else:
                dest_folder = target_path / "unknown"

            dest_file = dest_folder / photo_file.name

            if photo_file.resolve() != dest_file.resolve():
                files_to_move += 1
                file_list.append({
                    'source': str(photo_file.relative_to(source_path)),
                    'destination': str(dest_file.relative_to(target_path))
                })
            else:
                files_staying += 1

        operation = 'copy' if keep_original else 'move'

        return jsonify({
            'total_files': total_files,
            'files_to_move': files_to_move,
            'files_staying': files_staying,
            'file_list': file_list,
            'operation': operation
        })

    @app.route("/utilities/organize-photos/start", methods=["POST"])
    def utilities_organize_photos_start():
        from yaffo.utils.photo_dates import get_photo_date
        from calendar import month_name

        data = request.get_json()
        source_directory = data.get('source_directory')
        destination_directory = data.get('destination_directory')
        pattern = data.get('pattern')
        keep_original = data.get('keep_original', False)

        if not source_directory:
            return jsonify({'error': 'Source directory is required'}), 400

        source_path = Path(source_directory)
        if not source_path.exists():
            return jsonify({'error': 'Source directory does not exist'}), 400

        if not source_path.is_dir():
            return jsonify({'error': 'Source path is not a directory'}), 400

        if destination_directory:
            target_path = Path(destination_directory)
            if not target_path.exists():
                return jsonify({'error': 'Destination directory does not exist'}), 400
            if not target_path.is_dir():
                return jsonify({'error': 'Destination path is not a directory'}), 400
        else:
            target_path = source_path

        thumbnail_dir = get_thumbnail_dir()

        photo_files = []
        for p in source_path.rglob("*"):
            if not (p.suffix.lower() in PHOTO_EXTENSIONS and not p.name.startswith(".") and p.is_file()):
                continue

            if thumbnail_dir and p.is_relative_to(thumbnail_dir):
                continue

            if is_system_file(p.name):
                continue

            photo_files.append(p)

        file_operations = []
        operation_type = 'copy' if keep_original else 'move'

        for photo_file in photo_files:
            date_taken = get_photo_date(str(photo_file), None)

            if date_taken:
                if pattern == 'year_month':
                    dest_folder = target_path / str(date_taken.year) / month_name[date_taken.month]
                elif pattern == 'year_month_day':
                    dest_folder = target_path / str(date_taken.year) / month_name[date_taken.month] / f"{date_taken.day:02d}"
                elif pattern == 'year':
                    dest_folder = target_path / str(date_taken.year)
                else:
                    dest_folder = target_path / "unknown"
            else:
                dest_folder = target_path / "unknown"

            dest_file = dest_folder / photo_file.name

            if photo_file.resolve() != dest_file.resolve():
                file_operations.append({
                    'source': str(photo_file),
                    'destination': str(dest_file),
                    'type': operation_type
                })

        if not file_operations:
            return jsonify({'error': 'No files to organize'}), 400

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            name='organize_photos',
            status=JOB_STATUS_PENDING,
            task_count=len(file_operations),
            message=f'{"Copied" if keep_original else "Moved"} {{totalCount}}/{{taskCount}} photos',
            completed_count=0,
            error_count=0,
            cancelled_count=0,
            job_data=json.dumps({
                'source_directory': source_directory,
                'destination_directory': destination_directory,
                'pattern': pattern,
                'keep_original': keep_original,
                'operation_type': operation_type
            })
        )
        db.session.add(job)
        db.session.commit()

        for batch in batched(file_operations, 100):
            organize_photos_task(job_id=job_id, file_operations=list(batch))
        schedule_job_completion(job_id)
        return jsonify({'job_id': job_id}), 202
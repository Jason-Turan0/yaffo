from flask import Flask, render_template, request, jsonify
from yaffo.db import db
from yaffo.db.models import ApplicationSettings, Face
from yaffo.common import DB_PATH, HUEY_DB_PATH
import json
import subprocess
import platform
import shutil
from pathlib import Path


def init_settings_routes(app: Flask):
    def get_thumbnail_stats(directory: Path| None):
        """Get count and total size of thumbnails in directory"""
        if directory is None or not directory.exists():
            return 0, 0

        count = 0
        total_size = 0

        try:
            for file_path in directory.rglob("*"):
                if file_path.is_file():
                    count += 1
                    total_size += file_path.stat().st_size
        except Exception as e:
            print(f"Error getting thumbnail stats: {e}")

        return count, total_size

    def format_size(bytes_size: int) -> str:
        """Format bytes to human readable size"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} TB"

    @app.route("/settings", methods=["GET"])
    def settings_index():
        # Get media_dirs setting
        media_dirs_setting = db.session.query(ApplicationSettings).filter_by(name="media_dirs").first()

        if media_dirs_setting and media_dirs_setting.value:
            media_dirs = json.loads(media_dirs_setting.value)
        else:
            # Initialize with empty list if not exists
            media_dirs = []
            if not media_dirs_setting:
                media_dirs_setting = ApplicationSettings(
                    name="media_dirs",
                    type="json",
                    value="[]"
                )
                db.session.add(media_dirs_setting)
                db.session.commit()

        # Get thumbnail directory setting
        thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

        if thumbnail_setting and thumbnail_setting.value:
            current_thumbnail_dir = Path(thumbnail_setting.value)
        else:
            current_thumbnail_dir = None
        # Get thumbnail stats
        thumbnail_count, thumbnail_size = get_thumbnail_stats(current_thumbnail_dir)

        return render_template(
            "settings/index.html",
            media_dirs=media_dirs,
            db_path=str(DB_PATH),
            current_thumbnail_dir=str(current_thumbnail_dir) if current_thumbnail_dir else None,
            thumbnail_count=thumbnail_count,
            thumbnail_size=format_size(thumbnail_size),
            huey_db_path=str(HUEY_DB_PATH)
        )

    @app.route("/api/settings/media-dirs", methods=["POST"])
    def add_media_dir():
        """Add a new media directory"""
        data = request.get_json()
        new_dir = data.get("directory", "").strip()

        if not new_dir:
            return jsonify({"error": "Directory path is required"}), 400

        new_dir_path = Path(new_dir)

        try:
            new_dir_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return jsonify({"error": f"Failed to create directory: {str(e)}"}), 400

        # Get current media_dirs
        media_dirs_setting = db.session.query(ApplicationSettings).filter_by(name="media_dirs").first()

        if media_dirs_setting and media_dirs_setting.value:
            media_dirs = json.loads(media_dirs_setting.value)
        else:
            media_dirs = []

        # Check if directory already exists
        if new_dir in media_dirs:
            return jsonify({"error": "Directory already exists"}), 400

        # Add new directory
        media_dirs.append(new_dir)

        if media_dirs_setting:
            media_dirs_setting.value = json.dumps(media_dirs)
        else:
            media_dirs_setting = ApplicationSettings(
                name="media_dirs",
                type="json",
                value=json.dumps(media_dirs)
            )
            db.session.add(media_dirs_setting)

        db.session.commit()

        return jsonify({
            "success": True,
            "media_dirs": media_dirs
        })

    @app.route("/api/settings/media-dirs/<int:index>", methods=["DELETE"])
    def remove_media_dir(index: int):
        """Remove a media directory by index"""
        media_dirs_setting = db.session.query(ApplicationSettings).filter_by(name="media_dirs").first()

        if not media_dirs_setting or not media_dirs_setting.value:
            return jsonify({"error": "No media directories configured"}), 404

        media_dirs = json.loads(media_dirs_setting.value)

        if index < 0 or index >= len(media_dirs):
            return jsonify({"error": "Invalid index"}), 400

        # Remove directory
        removed_dir = media_dirs.pop(index)
        media_dirs_setting.value = json.dumps(media_dirs)
        db.session.commit()

        return jsonify({
            "success": True,
            "removed": removed_dir,
            "media_dirs": media_dirs
        })

    @app.route("/api/settings/select-folder", methods=["GET"])
    def select_folder():
        """Open a native folder selection dialog using OS-specific commands"""
        try:
            system = platform.system()
            folder_path = None

            if system == "Darwin":  # macOS
                # Use AppleScript to show folder picker
                script = '''
                tell application "System Events"
                    activate
                    set folderPath to choose folder with prompt "Select Media Directory"
                    return POSIX path of folderPath
                end tell
                '''
                result = subprocess.run(
                    ['osascript', '-e', script],
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minute timeout for user to select
                )
                if result.returncode == 0:
                    folder_path = result.stdout.strip()
                    # Remove trailing slash if present
                    if folder_path.endswith('/'):
                        folder_path = folder_path[:-1]

            elif system == "Windows":
                # Use PowerShell for Windows
                script = '''
                Add-Type -AssemblyName System.Windows.Forms
                $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
                $dialog.Description = "Select Media Directory"
                $result = $dialog.ShowDialog()
                if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
                    Write-Output $dialog.SelectedPath
                }
                '''
                result = subprocess.run(
                    ['powershell', '-Command', script],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode == 0 and result.stdout.strip():
                    folder_path = result.stdout.strip()

            else:  # Linux
                # For Linux, we'll return an error suggesting manual entry
                return jsonify({
                    "success": False,
                    "error": "Folder browser not available on Linux. Please enter path manually."
                }), 400

            if folder_path:
                return jsonify({
                    "success": True,
                    "path": folder_path
                })
            else:
                return jsonify({
                    "success": False,
                    "message": "No folder selected"
                })

        except subprocess.TimeoutExpired:
            return jsonify({
                "success": False,
                "error": "Folder selection timed out"
            }), 500
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @app.route("/api/settings/thumbnail-stats", methods=["GET"])
    def get_thumbnail_stats_api():
        """Get current thumbnail directory statistics"""
        # Get thumbnail_dir from settings or use default
        thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

        if thumbnail_setting and thumbnail_setting.value:
            thumbnail_dir = Path(thumbnail_setting.value)
        else:
            thumbnail_dir = None

        count, size = get_thumbnail_stats(thumbnail_dir)

        return jsonify({
            "success": True,
            "directory": str(thumbnail_dir),
            "count": count,
            "size": size,
            "size_formatted": format_size(size)
        })

    @app.route("/api/settings/thumbnail-dir", methods=["POST"])
    def update_thumbnail_dir():
        """Update thumbnail directory and move files"""
        data = request.get_json()
        new_dir = data.get("directory", "").strip()

        if not new_dir:
            return jsonify({"error": "Directory path is required"}), 400

        new_dir_path = Path(new_dir)

        # Get current thumbnail directory
        thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

        if thumbnail_setting and thumbnail_setting.value:
            current_dir = Path(thumbnail_setting.value)
        else:
            current_dir = None

        # Check if new directory is the same as current
        if current_dir and new_dir_path.resolve() == current_dir.resolve():
            return jsonify({"error": "New directory is the same as current directory"}), 400

        try:
            # Create new directory if it doesn't exist
            new_dir_path.mkdir(parents=True, exist_ok=True)

            # Get stats before moving
            file_count, total_size = get_thumbnail_stats(current_dir)

            # Track path mappings for database update
            path_mappings = {}

            # Move files
            if current_dir and current_dir.exists() and file_count > 0:
                for file_path in current_dir.rglob("*"):
                    if file_path.is_file():
                        relative_path = file_path.relative_to(current_dir)
                        dest_path = new_dir_path / relative_path
                        dest_path.parent.mkdir(parents=True, exist_ok=True)

                        # Track the mapping of old path to new path
                        path_mappings[str(file_path)] = str(dest_path)

                        shutil.move(str(file_path), str(dest_path))

            # Update Face table with new thumbnail paths
            faces_updated = 0
            if path_mappings:
                faces = db.session.query(Face).filter(Face.full_file_path.in_(path_mappings.keys())).all()
                for face in faces:
                    if face.full_file_path in path_mappings:
                        face.full_file_path = path_mappings[face.full_file_path]
                        faces_updated += 1

            # Update or create setting
            if thumbnail_setting:
                thumbnail_setting.value = str(new_dir_path)
            else:
                thumbnail_setting = ApplicationSettings(
                    name="thumbnail_dir",
                    type="str",
                    value=str(new_dir_path)
                )
                db.session.add(thumbnail_setting)

            db.session.commit()

            return jsonify({
                "success": True,
                "new_directory": str(new_dir_path),
                "files_moved": file_count,
                "faces_updated": faces_updated,
                "size_moved": format_size(total_size)
            })

        except Exception as e:
            db.session.rollback()
            return jsonify({
                "error": f"Failed to move thumbnails: {str(e)}"
            }), 500
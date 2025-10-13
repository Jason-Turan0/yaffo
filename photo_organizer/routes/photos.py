from photo_organizer.utils.image import convert_heif
from flask import Flask, send_from_directory, send_file, render_template, request, jsonify
from photo_organizer.common import ROOT_DIR
from photo_organizer.db.models import db, Photo, Person
from sqlalchemy.orm import joinedload
from photo_organizer.db.models import Face
import io
import os
import subprocess
import platform

def init_photos_routes(app: Flask):
    @app.route("/photos/<path:filename>")
    def photo(filename: str):
        file_path = ROOT_DIR / filename
        if file_path.suffix.lower() == ".heic":
            img = convert_heif(file_path)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/jpeg")
        return send_from_directory(ROOT_DIR, filename)

    @app.route("/photo/view/<path:filename>")
    def photo_view(filename: str):

        # Query the photo from the database
        photo = (Photo.query
                 .filter(Photo.relative_file_path == filename)
                 .options(joinedload(Photo.faces).joinedload(Face.people))
                 .first())

        if not photo:
            return "Photo not found", 404

        # Get unique people assigned to this photo
        people_set = set()
        for face in photo.faces:
            for person in face.people:
                people_set.add(person)

        people = sorted(people_set, key=lambda p: p.name)

        # Extract folder and filename
        folder = os.path.dirname(filename)
        file_name = os.path.basename(filename)

        # Calculate absolute paths for file and folder
        absolute_file_path = os.path.join(ROOT_DIR, filename)
        absolute_folder_path = os.path.join(ROOT_DIR, folder)

        return render_template(
            "photos/view.html",
            photo=photo,
            people=people,
            folder=folder,
            file_name=file_name,
            absolute_file_path=absolute_file_path,
            absolute_folder_path=absolute_folder_path
        )

    @app.route("/api/open-file", methods=["POST"])
    def open_file():
        data = request.get_json()
        file_path = data.get('path')

        if not file_path or not os.path.exists(file_path):
            return jsonify({"error": "File not found"}), 404

        try:
            system = platform.system()
            if system == "Darwin":  # macOS
                subprocess.run(["open", file_path], check=True)
            elif system == "Windows":
                os.startfile(file_path)
            else:  # Linux
                subprocess.run(["xdg-open", file_path], check=True)

            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/open-folder", methods=["POST"])
    def open_folder():
        data = request.get_json()
        folder_path = data.get('path')

        if not folder_path or not os.path.exists(folder_path):
            return jsonify({"error": "Folder not found"}), 404

        try:
            system = platform.system()
            if system == "Darwin":  # macOS
                subprocess.run(["open", folder_path], check=True)
            elif system == "Windows":
                os.startfile(folder_path)
            else:  # Linux
                subprocess.run(["xdg-open", folder_path], check=True)

            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
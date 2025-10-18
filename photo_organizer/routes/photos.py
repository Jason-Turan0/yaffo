from photo_organizer.utils.image import convert_heif
from flask import Flask, send_from_directory, send_file, render_template, request, jsonify
from photo_organizer.common import ROOT_DIR
from photo_organizer.db.models import db, Photo, Person, Tag
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
                 .options(
                     joinedload(Photo.faces).joinedload(Face.people),
                     joinedload(Photo.tags)
                 )
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

        # Prepare face data with locations for JavaScript
        faces_with_locations = []
        for face in photo.faces:
            if face.location_top is not None:
                faces_with_locations.append({
                    'id': face.id,
                    'thumbnail': face.relative_file_path,
                    'location': {
                        'top': face.location_top,
                        'right': face.location_right,
                        'bottom': face.location_bottom,
                        'left': face.location_left
                    },
                    'people': [{'id': p.id, 'name': p.name} for p in face.people]
                })

        tags_data = [
            {
                'id': tag.id,
                'tag_name': tag.tag_name,
                'tag_value': tag.tag_value
            }
            for tag in photo.tags
        ]

        return render_template(
            "photos/view.html",
            photo=photo,
            people=people,
            folder=folder,
            file_name=file_name,
            absolute_file_path=absolute_file_path,
            absolute_folder_path=absolute_folder_path,
            faces_with_locations=faces_with_locations,
            tags_data=tags_data
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

    @app.route("/api/photo/<int:photo_id>/tags", methods=["POST"])
    def add_photo_tag(photo_id: int):
        """Add a tag to a photo"""
        photo = db.session.get(Photo, photo_id)
        if not photo:
            return jsonify({"error": "Photo not found"}), 404

        data = request.get_json()
        tag_name = data.get("tag_name", "").strip()
        tag_value = data.get("tag_value", "").strip()

        if not tag_name:
            return jsonify({"error": "Tag name is required"}), 400

        # Create new tag
        tag = Tag(
            photo_id=photo_id,
            tag_name=tag_name,
            tag_value=tag_value if tag_value else None
        )
        db.session.add(tag)
        db.session.commit()

        return jsonify({
            "success": True,
            "tag": {
                "id": tag.id,
                "tag_name": tag.tag_name,
                "tag_value": tag.tag_value
            }
        })

    @app.route("/api/photo/tags/<int:tag_id>", methods=["PUT"])
    def update_photo_tag(tag_id: int):
        """Update a tag"""
        tag = db.session.get(Tag, tag_id)
        if not tag:
            return jsonify({"error": "Tag not found"}), 404

        data = request.get_json()
        tag_name = data.get("tag_name", "").strip()
        tag_value = data.get("tag_value", "").strip()

        if not tag_name:
            return jsonify({"error": "Tag name is required"}), 400

        tag.tag_name = tag_name
        tag.tag_value = tag_value if tag_value else None
        db.session.commit()

        return jsonify({
            "success": True,
            "tag": {
                "id": tag.id,
                "tag_name": tag.tag_name,
                "tag_value": tag.tag_value
            }
        })

    @app.route("/api/photo/tags/<int:tag_id>", methods=["DELETE"])
    def delete_photo_tag(tag_id: int):
        """Delete a tag"""
        tag = db.session.get(Tag, tag_id)
        if not tag:
            return jsonify({"error": "Tag not found"}), 404

        db.session.delete(tag)
        db.session.commit()

        return jsonify({"success": True})
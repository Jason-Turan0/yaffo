from yaffo.utils.image import convert_heif
from flask import Flask, Response, abort, send_from_directory, send_file, render_template, request, jsonify
from yaffo.background_tasks.events import emit_event
from yaffo.db.models import db, MediaItem, Person, Tag, EVENT_MEDIA_MODIFIED
from sqlalchemy.orm import joinedload
from yaffo.db.models import Face
from pathlib import Path
from yaffo import themes
from yaffo.themes import get_theme
import io
import os
import subprocess
import platform


def init_media_routes(app: Flask):
    @app.route("/media/<int:media_item_id>")
    def media(media_item_id: int):
        media_item = db.session.get(MediaItem, media_item_id)
        if not media_item:
            return "Photo not found", 404

        file_path = Path(media_item.full_file_path)
        if not file_path.exists():
            return "File not found", 404

        if file_path.suffix.lower() == ".heic":
            img = convert_heif(file_path)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/jpeg")
        return send_file(file_path)

    @app.route("/placeholder")
    def placeholder():
        theme = get_theme()
        if not themes.is_builtin(theme):
            custom = themes.get_custom_theme(theme)
            if custom and custom.published_theme.placeholder_svg:
                response = Response(custom.published_theme.placeholder_svg, mimetype="image/svg+xml")
                response.headers["Cache-Control"] = "no-store"
                return response
            theme = themes.DEFAULT_THEME
        return send_from_directory(f'static/themes/{theme}', 'placeholder.svg')

    @app.route("/media-by-path")
    def media_by_path():
        file_path = Path(request.args.get("mediaPath", type=str))
        if not file_path.exists():
            return "File not found", 404

        if file_path.suffix.lower() == ".heic":
            img = convert_heif(file_path)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/jpeg")
        return send_file(file_path)

    @app.route("/faces/<int:face_id>")
    def face_thumbnail(face_id: int):
        face = db.session.get(Face, face_id)
        if not face:
            return "Face not found", 404

        file_path = Path(face.full_file_path)
        if not file_path.exists():
            return "File not found", 404

        return send_file(file_path)

    @app.route("/media/view/<int:media_item_id>")
    def media_view(media_item_id: int):

        # Query the photo from the database
        media_item = (MediaItem.query
                 .filter(MediaItem.id == media_item_id)
                 .options(
                     joinedload(MediaItem.faces).joinedload(Face.people),
                     joinedload(MediaItem.tags)
                 )
                 .first())

        if not media_item:
            abort(404)

        # Get unique people assigned to this photo
        people_set = set()
        for face in media_item.faces:
            for person in face.people:
                people_set.add(person)

        people = sorted(people_set, key=lambda p: p.name)

        # Extract folder and filename from absolute path
        file_path = Path(media_item.full_file_path)
        folder = str(file_path.parent)
        file_name = file_path.name

        # Use absolute paths directly
        absolute_file_path = str(file_path)
        absolute_folder_path = folder

        # Prepare face data with locations for JavaScript
        faces_with_locations = []
        for face in media_item.faces:
            if face.location_top is not None:
                faces_with_locations.append({
                    'id': face.id,
                    'thumbnail': face.id,
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
            for tag in media_item.tags
        ]

        all_people = Person.query.order_by(Person.name).all()
        all_people_data = [
            {
                'id': person.id,
                'name': person.name
            }
            for person in all_people
        ]

        return render_template(
            "media/view.html",
            media_item=media_item,
            people=people,
            folder=folder,
            file_name=file_name,
            absolute_file_path=absolute_file_path,
            absolute_folder_path=absolute_folder_path,
            faces_with_locations=faces_with_locations,
            tags_data=tags_data,
            all_people=all_people_data
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

    @app.route("/api/media/<int:media_item_id>/favorite", methods=["POST"])
    def toggle_media_favorite(media_item_id: int):
        """Toggle a photo's favorite flag between True and NULL (NULL = unset, so the
        export/filter treat it as nothing). Emits photo_modified so the export
        automation can write the keyword. Returns the new favorite state."""
        media_item = db.session.get(MediaItem, media_item_id)
        if not media_item:
            return jsonify({"error": "Photo not found"}), 404

        media_item.favorite = True if not media_item.favorite else None
        db.session.commit()

        emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": [media_item_id]})
        return jsonify({"favorite": media_item.favorite})

    @app.route("/api/media/<int:media_item_id>/tags", methods=["PUT"])
    def update_media_tags(media_item_id: int):
        """Replace a photo's full tag set in one request, then emit photo_modified so
        downstream automations (e.g. export_photo_tag) react once per save."""
        media_item = db.session.get(MediaItem, media_item_id)
        if not media_item:
            return jsonify({"error": "Photo not found"}), 404

        data = request.get_json(silent=True) or {}
        incoming = data.get("tags", [])
        if not isinstance(incoming, list):
            return jsonify({"error": "tags must be a list"}), 400

        normalized = []
        for item in incoming:
            if not isinstance(item, dict):
                return jsonify({"error": "each tag must be an object"}), 400
            tag_name = (item.get("tag_name") or "").strip()
            tag_value = (item.get("tag_value") or "").strip()
            if not tag_name:
                return jsonify({"error": "Tag name is required"}), 400
            normalized.append((tag_name, tag_value or None))

        # Replace the collection; delete-orphan cascade removes the old rows on flush.
        media_item.tags = [Tag(tag_name=name, tag_value=value) for name, value in normalized]
        db.session.commit()

        emit_event(EVENT_MEDIA_MODIFIED, {"media_item_ids": [media_item_id]})

        return jsonify({
            "success": True,
            "tags": [
                {"id": tag.id, "tag_name": tag.tag_name, "tag_value": tag.tag_value}
                for tag in media_item.tags
            ],
        })
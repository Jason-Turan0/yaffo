from photo_organizer.utils.image import convert_heif
from flask import Flask, send_from_directory, send_file
from photo_organizer.common import ROOT_DIR
import io

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
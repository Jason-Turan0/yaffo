import pillow_heif
from flask import Flask, send_from_directory, send_file
from photo_organizer.common import ROOT_DIR
from PIL import Image
import io

def init_photos_routes(app: Flask):
    @app.route("/photos/<path:filename>")
    def photo(filename: str):
        file_path = ROOT_DIR / filename
        if file_path.suffix.lower() == ".heic":
            heif_file = pillow_heif.read_heif(str(file_path))
            img = Image.frombytes(
                heif_file.mode,
                heif_file.size,
                heif_file.data,
                "raw",
                heif_file.mode,
                heif_file.stride,
            )
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/jpeg")
        return send_from_directory(ROOT_DIR, filename)
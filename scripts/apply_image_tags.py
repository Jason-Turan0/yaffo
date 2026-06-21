import platform
import subprocess
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from yaffo.common import DB_PATH
from yaffo.db.models import Photo, Face, Person
from PIL import Image, PngImagePlugin
import piexif
import shutil

engine = create_engine(f"sqlite:///{DB_PATH}")
session = sessionmaker(bind=engine)()

def is_exiftool_available():
    return shutil.which("exiftool") is not None

def update_photo_metadata():
    photos = Photo.query.all()
    system = platform.system().lower()
    is_mac = system == "darwin"
    use_exiftool = not is_mac and is_exiftool_available()

    for photo in photos:
        path = Path(photo.full_file_path)
        if not path.exists():
            print(f"File not found: {path}")
            continue

        ext = path.suffix.lower()
        try:
            # --- Prepare date_taken ---
            date_str = None
            if photo.date_taken:
                date_str = photo.date_taken
                if len(date_str) == 10:
                    date_str += " 00:00:00"

            # --- Collect people names ---
            names = set()
            for face in photo.faces:
                for person in face.people:
                    if person.name:
                        names.add(person.name)
            names_str = ", ".join(sorted(names)) if names else None

            # --- HEIC/HEIF: external tool ---
            if ext in (".heic", ".heif"):
                if not (is_mac or use_exiftool):
                    print(f"Skipping HEIC (no tool available): {path}")
                    continue

                # Check if already processed
                already_processed = False
                if use_exiftool:
                    result = subprocess.run(
                        ["exiftool", "-XMP:ProcessingStatus", "-s3", str(path)],
                        capture_output=True, text=True
                    )
                    if result.stdout.strip().lower() == "yes":
                        already_processed = True
                # sips does not easily read existing tags
                if already_processed:
                    print(f"Skipping already processed HEIC: {path}")
                    continue

                if is_mac:
                    if date_str:
                        subprocess.run(["sips", "--setProperty", "creationDate", date_str, str(path)], check=True)
                    print(f"Updated metadata with sips: {path}")
                elif use_exiftool:
                    args = ["exiftool", "-overwrite_original"]
                    if date_str:
                        args.append(f"-DateTimeOriginal={date_str}")
                    if names_str:
                        args.append(f"-XMP:PersonInImage={names_str}")
                    args.append("-XMP:ProcessingStatus=Yes")
                    args.append(str(path))
                    subprocess.run(args, check=True)
                    print(f"Updated metadata with exiftool: {path}")

            # --- JPEG: native piexif ---
            elif ext in (".jpg", ".jpeg"):
                img = Image.open(path)
                exif_dict = piexif.load(img.info.get("exif", b""))
                # Check processed
                if exif_dict["0th"].get(piexif.ImageIFD.ImageDescription, b"").decode(errors="ignore") == "Processed":
                    print(f"Skipping already processed JPEG: {path}")
                    continue
                if date_str:
                    exif_dict["0th"][piexif.ImageIFD.DateTime] = date_str.encode()
                    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = date_str.encode()
                    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = date_str.encode()
                if names_str:
                    exif_dict["0th"][piexif.ImageIFD.ImageDescription] = names_str.encode()
                # Mark as processed
                exif_dict["0th"][piexif.ImageIFD.Software] = b"Processed"
                exif_bytes = piexif.dump(exif_dict)
                img.save(path, "jpeg", exif=exif_bytes)
                print(f"Updated metadata (JPEG native): {path}")

            # --- PNG: native PngInfo ---
            elif ext == ".png":
                img = Image.open(path)
                processed = img.info.get("Processed", "").lower() == "yes"
                if processed:
                    print(f"Skipping already processed PNG: {path}")
                    continue
                png_info = PngImagePlugin.PngInfo()
                if date_str:
                    png_info.add_text("DateTaken", date_str)
                if names_str:
                    png_info.add_text("People", names_str)
                png_info.add_text("Processed", "Yes")
                img.save(path, pnginfo=png_info)
                print(f"Updated metadata (PNG native): {path}")

            else:
                print(f"Unsupported file format: {path}")

        except subprocess.CalledProcessError as e:
            print(f"Tool error for {path}: {e}")
        except Exception as e:
            print(f"Error updating {path}: {e}")


if __name__ == "__main__":
    update_photo_metadata()

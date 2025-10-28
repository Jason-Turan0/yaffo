import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest
from PIL import Image, PngImagePlugin
import piexif
import subprocess
import json
from yaffo.utils.exiftool_path import get_exiftool_path

from yaffo.utils.write_metadata import (
    write_photo_metadata,
    _write_heic_metadata,
    _write_jpeg_metadata,
    _write_png_metadata,
    _run_exiftool
)


@pytest.fixture
def test_image_path():
    """Path to the test image provided by the user."""
    return Path(__file__).parent / "test_data" / "jpg" / "Canon_40D.jpg"

@pytest.fixture
def test_heic_image_path():
    """Path to the test image provided by the user."""
    return Path(__file__).parent / "test_data" / "heic" / "IMG_5195.HEIC"

@pytest.fixture
def test_heic_image_path():
    """Path to the test image provided by the user."""
    return Path(__file__).parent / "test_data" / "heic" / "IMG_5195.HEIC"

@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    temp = tempfile.mkdtemp()
    yield Path(temp)
    shutil.rmtree(temp)


@pytest.fixture
def temp_jpeg(temp_dir):
    """Create a temporary JPEG file with minimal EXIF."""
    img_path = temp_dir / "test.jpg"
    img = Image.new('RGB', (200, 200), color='red')

    # Add minimal EXIF to avoid piexif errors with empty data
    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
    exif_bytes = piexif.dump(exif_dict)
    img.save(img_path, "JPEG", exif=exif_bytes)
    return img_path


@pytest.fixture
def temp_png(temp_dir):
    """Create a temporary PNG file."""
    img_path = temp_dir / "test.png"
    img = Image.new('RGB', (200, 200), color='blue')
    img.save(img_path, "PNG")
    return img_path


@pytest.fixture
def temp_jpeg_with_exif(temp_dir):
    """Create a JPEG with existing EXIF data."""
    img_path = temp_dir / "test_with_exif.jpg"
    img = Image.new('RGB', (200, 200), color='green')

    exif_dict = {
        "0th": {
            piexif.ImageIFD.Make: b"OriginalCamera",
        },
        "Exif": {}
    }

    exif_bytes = piexif.dump(exif_dict)
    img.save(img_path, "JPEG", exif=exif_bytes)
    return img_path


class TestWritePhotoMetadata:
    def test_write_photo_metadata_file_not_found(self):
        non_existent = Path("/nonexistent/photo.jpg")
        success, error = write_photo_metadata(non_existent)

        assert success is False
        assert "File not found" in error

    def test_write_photo_metadata_unsupported_format(self, temp_dir):
        unsupported_file = temp_dir / "test.bmp"
        unsupported_file.touch()

        success, error = write_photo_metadata(unsupported_file)

        assert success is False
        assert "Unsupported file format" in error

    @patch('yaffo.utils.write_metadata._write_jpeg_metadata')
    def test_write_photo_metadata_jpeg(self, mock_write_jpeg, temp_jpeg):
        mock_write_jpeg.return_value = (True, None)

        success, error = write_photo_metadata(
            temp_jpeg,
            date_taken="2024-01-15",
            location_name="Test Location",
            people_names=["Alice", "Bob"]
        )

        assert success is True
        mock_write_jpeg.assert_called_once()
        args = mock_write_jpeg.call_args[0]
        assert args[0] == temp_jpeg
        assert args[1] == "2024-01-15 00:00:00"  # Date should be extended
        assert args[2] == "Test Location"
        assert args[3] == ["Alice", "Bob"]

    @patch('yaffo.utils.write_metadata._write_png_metadata')
    def test_write_photo_metadata_png(self, mock_write_png, temp_png):
        mock_write_png.return_value = (True, None)

        success, error = write_photo_metadata(
            temp_png,
            date_taken="2024-01-15 10:30:00"
        )

        assert success is True
        mock_write_png.assert_called_once()

    def test_write_photo_metadata_date_extension(self, temp_jpeg):
        with patch('yaffo.utils.write_metadata._write_jpeg_metadata') as mock:
            mock.return_value = (True, None)

            # Short date should be extended with time
            write_photo_metadata(temp_jpeg, date_taken="2024-01-15")

            args = mock.call_args[0]
            assert args[1] == "2024-01-15 00:00:00"

    def test_write_photo_metadata_date_no_extension(self, temp_jpeg):
        with patch('yaffo.utils.write_metadata._write_jpeg_metadata') as mock:
            mock.return_value = (True, None)

            # Full datetime should not be modified
            write_photo_metadata(temp_jpeg, date_taken="2024-01-15 14:30:45")

            args = mock.call_args[0]
            assert args[1] == "2024-01-15 14:30:45"


class TestWriteJpegMetadata:
    @patch('yaffo.utils.write_metadata._HAS_EXIFTOOL', True)
    @patch('yaffo.utils.write_metadata._run_exiftool')
    def test_write_jpeg_with_exiftool(self, mock_exiftool, temp_jpeg):
        success, error = _write_jpeg_metadata(
            temp_jpeg,
            "2024:01:15 10:30:00",
            "New York",
            ["Alice", "Bob"]
        )

        assert success is True
        assert error is None
        mock_exiftool.assert_called_once()

        args = mock_exiftool.call_args[0][0]
        assert "-overwrite_original" in args
        assert "-DateTimeOriginal=2024:01:15 10:30:00" in args
        assert "-CreateDate=2024:01:15 10:30:00" in args
        assert "-XMP:Location=New York" in args
        assert "-XMP:PersonInImage+=Alice" in args
        assert "-XMP:PersonInImage+=Bob" in args

    @patch('yaffo.utils.write_metadata._HAS_EXIFTOOL', False)
    def test_write_jpeg_with_piexif(self, temp_jpeg):
        success, error = _write_jpeg_metadata(
            temp_jpeg,
            "2024:01:15 10:30:00",
            "New York",
            ["Alice", "Bob"]
        )

        assert success is True
        assert error is None

        # Verify the metadata was written
        img = Image.open(temp_jpeg)
        exif_dict = piexif.load(img.info.get("exif", b""))

        assert exif_dict["0th"][piexif.ImageIFD.DateTime] == b"2024:01:15 10:30:00"
        assert exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2024:01:15 10:30:00"
        assert exif_dict["0th"][piexif.ImageIFD.ImageDescription] == b"New York"
        assert exif_dict["0th"][piexif.ImageIFD.Artist] == b"Alice, Bob"

    @patch('yaffo.utils.write_metadata._HAS_EXIFTOOL', False)
    def test_write_jpeg_preserves_existing_exif(self, temp_jpeg_with_exif):
        success, error = _write_jpeg_metadata(
            temp_jpeg_with_exif,
            "2024:01:15 10:30:00",
            None,
            None
        )

        assert success is True

        # Verify original data is preserved
        img = Image.open(temp_jpeg_with_exif)
        exif_dict = piexif.load(img.info.get("exif", b""))
        assert exif_dict["0th"][piexif.ImageIFD.Make] == b"OriginalCamera"

    @patch('yaffo.utils.write_metadata._HAS_EXIFTOOL', False)
    def test_write_jpeg_people_sorted(self, temp_jpeg):
        success, error = _write_jpeg_metadata(
            temp_jpeg,
            None,
            None,
            ["Zara", "Alice", "Bob"]
        )

        assert success is True

        img = Image.open(temp_jpeg)
        exif_dict = piexif.load(img.info.get("exif", b""))
        # People should be sorted alphabetically
        assert exif_dict["0th"][piexif.ImageIFD.Artist] == b"Alice, Bob, Zara"


class TestWritePngMetadata:
    def test_write_png_metadata_basic(self, temp_png):
        success, error = _write_png_metadata(
            temp_png,
            "2024-01-15 10:30:00",
            "Test Location",
            ["Alice", "Bob"]
        )

        assert success is True
        assert error is None

        # Verify metadata was written
        img = Image.open(temp_png)
        assert img.info.get("DateTaken") == "2024-01-15 10:30:00"
        assert img.info.get("Location") == "Test Location"
        assert img.info.get("Person_0") == "Alice"
        assert img.info.get("Person_1") == "Bob"

    def test_write_png_metadata_preserves_existing(self, temp_dir):
        # Create PNG with existing metadata
        img_path = temp_dir / "test_with_meta.png"
        img = Image.new('RGB', (200, 200), color='yellow')
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("CustomField", "CustomValue")
        img.save(img_path, "PNG", pnginfo=png_info)

        success, error = _write_png_metadata(
            img_path,
            "2024-01-15 10:30:00",
            None,
            None
        )

        assert success is True

        # Verify existing metadata is preserved
        img = Image.open(img_path)
        assert img.info.get("CustomField") == "CustomValue"
        assert img.info.get("DateTaken") == "2024-01-15 10:30:00"

    def test_write_png_metadata_people_sorted(self, temp_png):
        success, error = _write_png_metadata(
            temp_png,
            None,
            None,
            ["Zara", "Alice", "Bob"]
        )

        assert success is True

        img = Image.open(temp_png)
        # People should be sorted alphabetically
        assert img.info.get("Person_0") == "Alice"
        assert img.info.get("Person_1") == "Bob"
        assert img.info.get("Person_2") == "Zara"

    def test_write_png_metadata_removes_old_people(self, temp_dir):
        # Create PNG with existing person metadata
        img_path = temp_dir / "test_with_people.png"
        img = Image.new('RGB', (200, 200), color='cyan')
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("Person_0", "OldPerson1")
        png_info.add_text("Person_1", "OldPerson2")
        png_info.add_text("CustomField", "KeepThis")
        img.save(img_path, "PNG", pnginfo=png_info)

        success, error = _write_png_metadata(
            img_path,
            None,
            None,
            ["NewPerson"]
        )

        assert success is True

        img = Image.open(img_path)
        # Old person tags should be removed
        assert img.info.get("Person_0") == "NewPerson"
        assert img.info.get("Person_1") is None
        # Other metadata should be preserved
        assert img.info.get("CustomField") == "KeepThis"


class TestWriteHeicMetadata:
    @patch('yaffo.utils.write_metadata._IS_MAC', False)
    @patch('yaffo.utils.write_metadata._HAS_EXIFTOOL', True)
    @patch('yaffo.utils.write_metadata._run_exiftool')
    def test_write_heic_exiftool(self, mock_exiftool, temp_dir):
        heic_path = temp_dir / "test.heic"
        heic_path.touch()

        success, error = _write_heic_metadata(
            heic_path,
            "2024:01:15 10:30:00",
            "Test Location",
            ["Alice"]
        )

        assert success is True
        mock_exiftool.assert_called_once()

        args = mock_exiftool.call_args[0][0]
        assert "-overwrite_original" in args
        assert "-DateTimeOriginal=2024:01:15 10:30:00" in args
        assert "-XMP:Location=Test Location" in args
        assert "-XMP:PersonInImage+=Alice" in args

    @patch('yaffo.utils.write_metadata._IS_MAC', False)
    @patch('yaffo.utils.write_metadata._HAS_EXIFTOOL', False)
    def test_write_heic_no_tool(self, temp_dir):
        heic_path = temp_dir / "test.heic"
        heic_path.touch()

        success, error = _write_heic_metadata(
            heic_path,
            "2024:01:15 10:30:00",
            None,
            None
        )

        assert success is False
        assert "No tool available" in error


class TestRunExiftool:
    @patch('yaffo.utils.write_metadata._EXIFTOOL_PATH', '/usr/bin/exiftool')
    @patch('yaffo.utils.write_metadata.subprocess.run')
    def test_run_exiftool_success(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stdout="Success")

        result = _run_exiftool(["-version"])

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0][0] == '/usr/bin/exiftool'
        assert call_args[0][0][1] == '-version'

    @patch('yaffo.utils.write_metadata._EXIFTOOL_PATH', None)
    def test_run_exiftool_not_available(self):
        with pytest.raises(FileNotFoundError, match="exiftool not available"):
            _run_exiftool(["-version"])

    @patch('yaffo.utils.write_metadata._EXIFTOOL_PATH', '/usr/bin/exiftool')
    @patch('yaffo.utils.write_metadata.subprocess.run')
    def test_run_exiftool_command_error(self, mock_run):
        mock_run.side_effect = Exception("Command failed")

        with pytest.raises(Exception, match="Command failed"):
            _run_exiftool(["-version"])


class TestIntegrationWithRealImage:
    def test_write_metadata_to_real_jpeg(self, test_image_path, temp_dir):
        # Copy the test image to temp directory
        test_copy = temp_dir / "test_copy.jpg"
        shutil.copy(test_image_path, test_copy)
        success, error = write_photo_metadata(
            test_copy,
            date_taken="2024-01-15 10:30:00",
            location_name="Test Location",
            people_names=["Alice", "Bob"]
        )

        assert success is True
        assert error is None

        exiftool_path = get_exiftool_path()
        result = subprocess.run(
            [str(exiftool_path), "-json", "-DateTimeOriginal", "-XMP:Location", "-XMP:PersonInImage", str(test_copy)],
            capture_output=True,
            text=True
        )

        metadata = json.loads(result.stdout)[0]
        assert metadata.get("DateTimeOriginal") == "2024:01:15 10:30:00"
        assert metadata.get("Location") == "Test Location"

        # PersonInImage can be a list or string depending on exiftool version
        person_in_image = metadata.get("PersonInImage")
        if isinstance(person_in_image, list):
            assert sorted(person_in_image) == ["Alice", "Bob"]
        else:
            # Single value or not set
            assert "Alice" in str(person_in_image) or "Bob" in str(person_in_image)
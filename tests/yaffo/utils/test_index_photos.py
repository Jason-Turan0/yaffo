import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest
import numpy as np
from PIL import Image
import piexif

from yaffo.utils.index_photos import (
    get_photo_files,
    save_face_thumbnail,
    convert_to_degrees,
    get_exif_data_with_exiftool,
    get_gps_coordinates,
    get_exif_tags,
    index_photo,
    delete_orphaned_photos
)


@pytest.fixture
def test_image_path():
    return Path(__file__).parent / "test_data" / "jpg" / "Canon_40D.jpg"


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    temp = tempfile.mkdtemp()
    yield Path(temp)
    shutil.rmtree(temp)


@pytest.fixture
def sample_photo_dir(temp_dir):
    """Create a sample directory structure with photo files."""
    photo_dir = temp_dir / "photos"
    photo_dir.mkdir()

    # Create some test files
    (photo_dir / "photo1.jpg").touch()
    (photo_dir / "photo2.JPEG").touch()
    (photo_dir / "photo3.png").touch()
    (photo_dir / "photo4.heic").touch()
    (photo_dir / ".hidden.jpg").touch()  # Should be excluded
    (photo_dir / "document.txt").touch()  # Should be excluded

    # Create subdirectory with photos
    subdir = photo_dir / "subdir"
    subdir.mkdir()
    (subdir / "photo5.jpg").touch()

    return photo_dir


@pytest.fixture
def test_image_with_exif(temp_dir):
    """Create a test JPEG image with EXIF data."""
    img_path = temp_dir / "test_with_exif.jpg"
    img = Image.new('RGB', (100, 100), color='red')

    # Create EXIF data with GPS coordinates
    exif_dict = {
        "0th": {
            piexif.ImageIFD.Make: b"TestCamera",
            piexif.ImageIFD.Model: b"TestModel",
            piexif.ImageIFD.DateTime: b"2024:01:15 10:30:00"
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2024:01:15 10:30:00",
            piexif.ExifIFD.DateTimeDigitized: b"2024:01:15 10:30:00",
            piexif.ExifIFD.LensModel: b"TestLens"
        },
        "GPS": {
            piexif.GPSIFD.GPSLatitude: ((40, 1), (42, 1), (51, 1)),
            piexif.GPSIFD.GPSLatitudeRef: b'N',
            piexif.GPSIFD.GPSLongitude: ((74, 1), (0, 1), (21, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b'W'
        }
    }

    exif_bytes = piexif.dump(exif_dict)
    img.save(img_path, "JPEG", exif=exif_bytes)

    return img_path


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = Mock()
    session.query = Mock()
    session.add = Mock()
    session.commit = Mock()
    session.flush = Mock()
    return session


class TestGetPhotoFiles:
    def test_get_photo_files_finds_all_photos(self, sample_photo_dir):
        files = get_photo_files(sample_photo_dir)

        # Should find 5 photos (4 in root, 1 in subdir), excluding .hidden.jpg and document.txt
        assert len(files) == 5

        # Check that all files have photo extensions
        for file in files:
            assert file.suffix.lower() in {'.jpg', '.jpeg', '.png', '.heic'}

    def test_get_photo_files_excludes_hidden(self, sample_photo_dir):
        files = get_photo_files(sample_photo_dir)

        # Hidden files should be excluded
        hidden_files = [f for f in files if f.name.startswith('.')]
        assert len(hidden_files) == 0

    def test_get_photo_files_empty_directory(self, temp_dir):
        empty_dir = temp_dir / "empty"
        empty_dir.mkdir()

        files = get_photo_files(empty_dir)
        assert len(files) == 0

    def test_get_photo_files_recursive(self, sample_photo_dir):
        files = get_photo_files(sample_photo_dir)

        # Should find files in subdirectories
        subdir_files = [f for f in files if "subdir" in str(f)]
        assert len(subdir_files) == 1


class TestSaveFaceThumbnail:
    def test_save_face_thumbnail_creates_file(self, test_image_path, temp_dir):
        if not test_image_path.exists():
            pytest.skip("test.jpg not provided yet")

        # Mock face location (top, right, bottom, left)
        face_location = (50, 150, 150, 50)

        thumb_path = save_face_thumbnail(test_image_path, 0, temp_dir, face_location)

        assert thumb_path.exists()
        assert thumb_path.suffix == '.jpg'
        assert thumb_path.parent == temp_dir
        assert "face_" in thumb_path.name

    def test_save_face_thumbnail_correct_dimensions(self, test_image_path, temp_dir):
        if not test_image_path.exists():
            pytest.skip("test.jpg not provided yet")

        face_location = (50, 150, 150, 50)
        thumb_path = save_face_thumbnail(test_image_path, 0, temp_dir, face_location)

        # Check thumbnail dimensions (should be max 150x150)
        img = Image.open(thumb_path)
        assert img.width <= 150
        assert img.height <= 150

    def test_save_face_thumbnail_unique_names(self, test_image_path, temp_dir):
        if not test_image_path.exists():
            pytest.skip("test.jpg not provided yet")

        face_location = (50, 150, 150, 50)

        thumb1 = save_face_thumbnail(test_image_path, 0, temp_dir, face_location)
        thumb2 = save_face_thumbnail(test_image_path, 1, temp_dir, face_location)

        # Should create unique filenames
        assert thumb1 != thumb2


class TestConvertToDegrees:
    def test_convert_to_degrees_basic(self):
        # 40 degrees, 42 minutes, 51 seconds = 40.7141666...
        value = ((40, 1), (42, 1), (51, 1))
        result = convert_to_degrees(value)

        assert abs(result - 40.7141666) < 0.0001

    def test_convert_to_degrees_fractional(self):
        # Test with fractional values
        value = ((40, 2), (30, 2), (0, 1))
        result = convert_to_degrees(value)

        # 40/2 + 30/(2*60) + 0 = 20 + 0.25 = 20.25
        assert abs(result - 20.25) < 0.0001

    def test_convert_to_degrees_zero(self):
        value = ((0, 1), (0, 1), (0, 1))
        result = convert_to_degrees(value)

        assert result == 0.0


class TestGetGpsCoordinates:
    def test_get_gps_coordinates_valid_data(self, test_image_with_exif):
        img = Image.open(test_image_with_exif)

        latitude, longitude, location_name = get_gps_coordinates(img)

        # Expected: 40°42'51"N = ~40.7141666
        # Expected: 74°0'21"W = ~-74.0058333
        assert latitude is not None
        assert longitude is not None
        assert abs(latitude - 40.7141666) < 0.0001
        assert abs(longitude - (-74.0058333)) < 0.0001

    def test_get_gps_coordinates_no_exif(self, temp_dir):
        # Create image without EXIF
        img_path = temp_dir / "no_exif.jpg"
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(img_path, "JPEG")

        img = Image.open(img_path)
        latitude, longitude, location_name = get_gps_coordinates(img)

        assert latitude is None
        assert longitude is None
        assert location_name is None

    def test_get_gps_coordinates_no_gps_data(self, temp_dir):
        # Create image with EXIF but no GPS
        img_path = temp_dir / "no_gps.jpg"
        img = Image.new('RGB', (100, 100), color='green')

        exif_dict = {
            "0th": {piexif.ImageIFD.Make: b"TestCamera"},
            "Exif": {}
        }

        exif_bytes = piexif.dump(exif_dict)
        img.save(img_path, "JPEG", exif=exif_bytes)

        img = Image.open(img_path)
        latitude, longitude, location_name = get_gps_coordinates(img)

        assert latitude is None
        assert longitude is None


class TestGetExifTags:
    def test_get_exif_tags_valid_data(self, test_image_with_exif):
        img = Image.open(test_image_with_exif)
        tags = get_exif_tags(img)

        assert isinstance(tags, list)
        assert len(tags) > 0

        # Check tag structure
        for tag in tags:
            assert 'tag_name' in tag
            assert 'tag_value' in tag
            assert isinstance(tag['tag_name'], str)
            assert isinstance(tag['tag_value'], str)

    def test_get_exif_tags_excludes_datetime(self, test_image_with_exif):
        img = Image.open(test_image_with_exif)
        tags = get_exif_tags(img)

        # DateTime tags should be excluded
        datetime_tags = [t for t in tags if 'DateTime' in t['tag_name']]
        assert len(datetime_tags) == 0

    def test_get_exif_tags_no_exif(self, temp_dir):
        img_path = temp_dir / "no_tags.jpg"
        img = Image.new('RGB', (100, 100), color='yellow')
        img.save(img_path, "JPEG")

        img = Image.open(img_path)
        tags = get_exif_tags(img)

        assert isinstance(tags, list)
        assert len(tags) == 0

class TestIndexPhoto:
    @patch('yaffo.utils.index_photos.face_recognition')
    @patch('yaffo.utils.index_photos.save_face_thumbnail')
    def test_index_photo_no_faces(self, mock_save_thumb, mock_fr, test_image_with_exif, temp_dir):
        # Mock face recognition to find no faces
        mock_fr.face_locations.return_value = []
        mock_fr.face_encodings.return_value = []

        result = index_photo(test_image_with_exif, temp_dir)

        assert result is not None
        assert 'latitude' in result
        assert 'longitude' in result
        assert 'tags' in result
        assert 'faces' in result
        assert len(result['faces']) == 0

    @patch('yaffo.utils.index_photos.face_recognition')
    @patch('yaffo.utils.index_photos.save_face_thumbnail')
    def test_index_photo_with_faces(self, mock_save_thumb, mock_fr, test_image_with_exif, temp_dir):
        # Mock face recognition to find one face
        mock_face_location = (50, 150, 150, 50)
        mock_embedding = np.array([0.1] * 128)

        mock_fr.face_locations.return_value = [mock_face_location]
        mock_fr.face_encodings.return_value = [mock_embedding]
        mock_save_thumb.return_value = temp_dir / "face_thumb.jpg"

        result = index_photo(test_image_with_exif, temp_dir)

        assert result is not None
        assert len(result['faces']) == 1

        face = result['faces'][0]
        assert 'embedding' in face
        assert 'location_top' in face
        assert 'location_right' in face
        assert 'location_bottom' in face
        assert 'location_left' in face
        assert face['location_top'] == 50
        assert face['location_right'] == 150

    def test_index_photo_extracts_gps(self, test_image_with_exif, temp_dir):
        with patch('yaffo.utils.index_photos.face_recognition') as mock_fr:
            mock_fr.face_locations.return_value = []
            mock_fr.face_encodings.return_value = []

            result = index_photo(test_image_with_exif, temp_dir)

            assert result is not None
            assert result['latitude'] is not None
            assert result['longitude'] is not None


class TestDeleteOrphanedPhotos:
    def test_delete_orphaned_photos_success(self, mock_db_session):
        photo_ids = [1, 2, 3]

        # Mock the query chains
        mock_face_query = Mock()
        mock_photo_query = Mock()

        mock_face_query.filter.return_value.delete.return_value = 5
        mock_photo_query.filter.return_value.delete.return_value = 3

        # Setup query to return appropriate mocks
        def query_side_effect(model):
            from yaffo.db.models import Face, Photo
            if model == Face:
                return mock_face_query
            elif model == Photo:
                return mock_photo_query

        mock_db_session.query.side_effect = query_side_effect

        deleted = delete_orphaned_photos(mock_db_session, photo_ids)

        assert deleted == 3
        mock_db_session.commit.assert_called_once()

    def test_delete_orphaned_photos_empty_list(self, mock_db_session):
        deleted = delete_orphaned_photos(mock_db_session, [])

        assert deleted == 0
        mock_db_session.query.assert_not_called()


class TestGetExifDataWithExiftool:
    @patch('yaffo.utils.index_photos.subprocess.run')
    @patch('yaffo.utils.index_photos._EXIFTOOL_PATH', '/usr/bin/exiftool')
    def test_get_exif_data_success(self, mock_run):
        test_path = Path("/test/photo.jpg")

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = '[{"Make": "Canon", "Model": "EOS 5D"}]'
        mock_run.return_value = mock_result

        result = get_exif_data_with_exiftool(test_path)

        assert result is not None
        assert result['Make'] == 'Canon'
        assert result['Model'] == 'EOS 5D'

    @patch('yaffo.utils.index_photos._EXIFTOOL_PATH', None)
    def test_get_exif_data_no_exiftool(self):
        test_path = Path("/test/photo.jpg")

        result = get_exif_data_with_exiftool(test_path)

        assert result is None

    @patch('yaffo.utils.index_photos.subprocess.run')
    @patch('yaffo.utils.index_photos._EXIFTOOL_PATH', '/usr/bin/exiftool')
    def test_get_exif_data_error(self, mock_run):
        test_path = Path("/test/photo.jpg")

        mock_result = Mock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        result = get_exif_data_with_exiftool(test_path)

        assert result is None
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import piexif
from PIL import Image


@dataclass
class PhotoDateInfo:
    date: Optional[datetime] = None
    year: Optional[int] = None
    month: Optional[int] = None


def _parse_full_date_match(
    groups: tuple[str, ...], pattern_type: str
) -> PhotoDateInfo | None:
    try:
        if pattern_type == "MMDDYY":
            month, day, year_short = groups
            year = 2000 + int(year_short) if int(year_short) < 50 else 1900 + int(year_short)
            month_int, day_int = int(month), int(day)
        elif pattern_type == "DD-MM-YYYY":
            day, month, year = groups
            year = int(year)
            month_int, day_int = int(month), int(day)
        else:
            year, month, day = groups
            year = int(year)
            month_int, day_int = int(month), int(day)

        if 1 <= month_int <= 12 and 1 <= day_int <= 31:
            return PhotoDateInfo(
                date=datetime(year, month_int, day_int),
                year=year,
                month=month_int
            )
    except (ValueError, TypeError):
        pass
    return None


def _results_conflict(results: list[PhotoDateInfo]) -> bool:
    if len(results) <= 1:
        return False

    years = {r.year for r in results if r.year is not None}
    months = {r.month for r in results if r.month is not None}
    dates = {r.date for r in results if r.date is not None}

    return len(years) > 1 or len(months) > 1 or len(dates) > 1


def _get_best_result(results: list[PhotoDateInfo]) -> PhotoDateInfo:
    if not results:
        return PhotoDateInfo()

    full_dates = [r for r in results if r.date is not None]
    if full_dates:
        return full_dates[0]

    month_years = [r for r in results if r.year is not None and r.month is not None]
    if month_years:
        return month_years[0]

    year_only = [r for r in results if r.year is not None]
    if year_only:
        return year_only[0]

    return PhotoDateInfo()


def get_date_from_filename(filename: str) -> PhotoDateInfo:
    full_date_results: list[PhotoDateInfo] = []
    month_year_results: list[PhotoDateInfo] = []
    year_only_results: list[PhotoDateInfo] = []

    full_date_patterns = [
        (r"(\d{4})(\d{2})(\d{2})", "YYYYMMDD"),
        (r"(\d{4})[-_](\d{2})[-_](\d{2})", "YYYY-MM-DD"),
        (r"(?:IMG|DSC|PXL|VID)[-_]?(\d{4})(\d{2})(\d{2})", "IMG_YYYYMMDD"),
        (r"(\d{2})[-_](\d{2})[-_](\d{4})", "DD-MM-YYYY"),
        (r"(\d{2})(\d{2})(\d{2})(?:[A-Z]{2})?[-_]", "MMDDYY"),
    ]

    for pattern, pattern_type in full_date_patterns:
        for match in re.finditer(pattern, filename):
            result = _parse_full_date_match(match.groups(), pattern_type)
            if result:
                full_date_results.append(result)

    month_year_patterns = [
        (r"(\d{2})(\d{4})", "MMYYYY"),
    ]

    for pattern, _ in month_year_patterns:
        for match in re.finditer(pattern, filename):
            try:
                month_str, year_str = match.groups()
                month_int, year_int = int(month_str), int(year_str)
                if 1 <= month_int <= 12 and 1900 <= year_int <= 2100:
                    month_year_results.append(PhotoDateInfo(date=None, year=year_int, month=month_int))
            except (ValueError, TypeError):
                pass

    year_patterns = [
        r"[_-](19\d{2}|20\d{2})[_-]",
        r"[_-](19\d{2}|20\d{2})\.",
        r"^(19\d{2}|20\d{2})[_-]",
    ]

    for pattern in year_patterns:
        for match in re.finditer(pattern, filename):
            try:
                year_int = int(match.group(1))
                if 1900 <= year_int <= 2100:
                    year_only_results.append(PhotoDateInfo(date=None, year=year_int, month=None))
            except (ValueError, TypeError):
                pass

    all_results = full_date_results + month_year_results + year_only_results

    if _results_conflict(all_results):
        return PhotoDateInfo()

    if full_date_results:
        return _get_best_result(full_date_results)
    if month_year_results:
        return _get_best_result(month_year_results)
    if year_only_results:
        return _get_best_result(year_only_results)

    return PhotoDateInfo()


def get_date_from_metadata(path: str, metadata: Optional[dict]):
    try:
        if metadata is not None:
            date_original = metadata.get("DateTimeOriginal")
            if date_original is not None:
                return datetime.strptime(date_original, "%Y:%m:%d %H:%M:%S")


        img = Image.open(path)
        exif_data = img.info.get("exif")
        if exif_data:
            exif_dict = piexif.load(exif_data)
            date_str = exif_dict["Exif"].get(piexif.ExifIFD.DateTimeOriginal)
            if date_str:
                return datetime.strptime(date_str.decode(), "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass


def get_photo_date_info(path: str, data: Optional[dict]) -> PhotoDateInfo:
    """
    Extract photo date info in this order:
    1. Filename patterns (full date, month+year, or year only)
    2. EXIF 'DateTimeOriginal'
    3. File modified date (only for full date)
    """
    date_info = get_date_from_filename(path)
    if date_info.date is not None or date_info.year is not None:
        return date_info

    date_from_metadata = get_date_from_metadata(path, data)
    if date_from_metadata is not None:
        return PhotoDateInfo(
            date=date_from_metadata,
            year=date_from_metadata.year,
            month=date_from_metadata.month
        )

    try:
        ts = os.path.getmtime(path)
        file_date = datetime.fromtimestamp(ts)
        return PhotoDateInfo(
            date=file_date,
            year=file_date.year,
            month=file_date.month
        )
    except Exception:
        return PhotoDateInfo()


def get_photo_date(path: str, data: Optional[dict]) -> Optional[datetime]:
    """
    Extract photo date (backward compatible wrapper).
    Returns full datetime or None.
    """
    date_info = get_photo_date_info(path, data)
    return date_info.date

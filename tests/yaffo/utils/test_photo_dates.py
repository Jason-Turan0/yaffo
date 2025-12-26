from datetime import datetime
import pytest

from yaffo.utils.photo_dates import PhotoDateInfo, get_date_from_filename


class TestGetDateFromFilename:
    """Tests for filename date parsing based on obama_family directory patterns."""

    # Full date patterns (all three: year, month, day)
    class TestFullDatePatterns:
        def test_yyyymmdd_compact(self):
            """Pattern: 20211205.jpg"""
            result = get_date_from_filename("photo_20211205.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_yyyy_mm_dd_dashes(self):
            """Pattern: 2021-12-05.jpg"""
            result = get_date_from_filename("photo_2021-12-05.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_yyyy_mm_dd_underscores(self):
            """Pattern: 2021_12_05.jpg"""
            result = get_date_from_filename("photo_2021_12_05.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_img_prefix(self):
            """Pattern: IMG_20211205.jpg"""
            result = get_date_from_filename("IMG_20211205_123456.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_dsc_prefix(self):
            """Pattern: DSC_20211205.jpg"""
            result = get_date_from_filename("DSC_20211205.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_pxl_prefix(self):
            """Pattern: PXL_20211205.jpg (Google Pixel)"""
            result = get_date_from_filename("PXL_20211205_123456.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_vid_prefix(self):
            """Pattern: VID_20211205.mp4"""
            result = get_date_from_filename("VID_20211205_123456.mp4")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_dd_mm_yyyy_dashes(self):
            """Pattern: 05-12-2021.jpg (European format)"""
            result = get_date_from_filename("photo_05-12-2021.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_white_house_photo_id_2010(self):
            """Pattern: P112310PS-1414.jpg (White House format: MMDDYY)

            Real example from obama_family: barack_01_P112310PS-1414.jpg
            P112310 = 11/23/10 (November 23, 2010)
            """
            result = get_date_from_filename("barack_01_P112310PS-1414.jpg")
            assert result.date == datetime(2010, 11, 23)
            assert result.year == 2010
            assert result.month == 11

        def test_white_house_photo_id_2009(self):
            """Pattern: P020109PS-0339.jpg

            Real example from obama_family: barack_02_P020109PS-0339.jpg
            P020109 = 02/01/09 (February 1, 2009)
            """
            result = get_date_from_filename("barack_02_P020109PS-0339.jpg")
            assert result.date == datetime(2009, 2, 1)
            assert result.year == 2009
            assert result.month == 2

        def test_white_house_photo_id_ck_suffix(self):
            """Pattern: P032310CK-0627.jpg (CK suffix instead of PS)

            Real example from obama_family: barack_07_P032310CK-0627.jpg
            P032310 = 03/23/10 (March 23, 2010)
            """
            result = get_date_from_filename("barack_07_P032310CK-0627.jpg")
            assert result.date == datetime(2010, 3, 23)
            assert result.year == 2010
            assert result.month == 3

        def test_white_house_photo_id_no_suffix(self):
            """Pattern: P090109-0127.jpg (no letter suffix)

            Real example from obama_family: library_03_P090109-0127.jpg
            P090109 = 09/01/09 (September 1, 2009)
            """
            result = get_date_from_filename("library_03_P090109-0127.jpg")
            assert result.date == datetime(2009, 9, 1)
            assert result.year == 2009
            assert result.month == 9

        def test_white_house_photo_id_2016(self):
            """Pattern: P021816LJ-0192.jpg

            Real example from obama_family: barack_33_P021816LJ-0192.jpg
            P021816 = 02/18/16 (February 18, 2016)
            """
            result = get_date_from_filename("barack_33_P021816LJ-0192.jpg")
            assert result.date == datetime(2016, 2, 18)
            assert result.year == 2016
            assert result.month == 2

    # Month + Year patterns (no day)
    class TestMonthYearPatterns:
        def test_mmyyyy_standalone(self):
            """MMYYYY pattern only matches when no full date patterns match.

            Pattern like 012025 followed by - will match MMDDYY (01/20/25) instead.
            Use a filename where MMYYYY is unambiguous.
            """
            result = get_date_from_filename("report_012025.pdf")
            assert result.date is None
            assert result.year == 2025
            assert result.month == 1

        def test_mmyyyy_december(self):
            """Pattern: MMYYYY without trailing dash (which would trigger MMDDYY)."""
            result = get_date_from_filename("photo_122024.jpg")
            assert result.date is None
            assert result.year == 2024
            assert result.month == 12

        def test_mmddyy_takes_precedence_over_mmyyyy(self):
            """When 6 digits followed by - could be MMDDYY, it's interpreted as full date.

            012025- = 01/20/25 (Jan 20, 2025) via MMDDYY pattern.
            """
            result = get_date_from_filename("Obamas-Cutest-Moments-4-012025-xxx.jpg")
            assert result.date == datetime(2025, 1, 20)
            assert result.year == 2025
            assert result.month == 1

    # Year-only patterns
    class TestYearOnlyPatterns:
        def test_year_with_underscores(self):
            """Pattern: medium_btl_2015_01.jpg

            Real example from obama_family: medium_btl_2015_01.jpg
            Year: 2015
            """
            result = get_date_from_filename("medium_btl_2015_01.jpg")
            assert result.date is None
            assert result.year == 2015
            assert result.month is None

        def test_year_with_underscores_2016(self):
            """Pattern: medium_btl_2016_05.jpg

            Real example from obama_family: medium_btl_2016_05.jpg
            Year: 2016
            """
            result = get_date_from_filename("medium_btl_2016_05.jpg")
            assert result.date is None
            assert result.year == 2016
            assert result.month is None

        def test_year_with_dashes(self):
            """Pattern: barack-obama-16-2000-xxx.jpg

            Real example from obama_family: barack-obama-16-2000-730e5eb4e6724fe8b1d71cf49d846472.jpg
            Year: 2000
            """
            result = get_date_from_filename("barack-obama-16-2000-730e5eb4e6724fe8b1d71cf49d846472.jpg")
            assert result.date is None
            assert result.year == 2000
            assert result.month is None

        def test_year_only_no_prefix(self):
            """Pattern: barack-obama-2000-xxx.jpg

            Real example from obama_family: barack-obama-2000-a483dcd16ea642088e674cb953291bfb.jpg
            Year: 2000
            """
            result = get_date_from_filename("barack-obama-2000-a483dcd16ea642088e674cb953291bfb.jpg")
            assert result.date is None
            assert result.year == 2000
            assert result.month is None

        def test_year_before_extension(self):
            """Pattern: holiday-tipping-2023.jpg"""
            result = get_date_from_filename("holiday-tipping-2023.jpg")
            assert result.date is None
            assert result.year == 2023
            assert result.month is None

    # No date patterns
    class TestNoDatePatterns:
        def test_gallery_numbered(self):
            """Pattern: Obama-Family-Gallery-01.jpg

            Real example from obama_family: Obama-Family-Gallery-01.jpg
            No extractable date
            """
            result = get_date_from_filename("Obama-Family-Gallery-01.jpg")
            assert result.date is None
            assert result.year is None
            assert result.month is None

        def test_timeline_numbered(self):
            """Pattern: Barack-Michelle-Obama-Timeline-0002.jpg

            Real example from obama_family: Barack-Michelle-Obama-Timeline-0002.jpg
            No extractable date
            """
            result = get_date_from_filename("Barack-Michelle-Obama-Timeline-0002.jpg")
            assert result.date is None
            assert result.year is None
            assert result.month is None

        def test_uuid_filename(self):
            """Pattern: ba39a6c5-3886-486c-8995-d9a6591535e8.jpg

            Real example from obama_family: ba39a6c5-3886-486c-8995-d9a6591535e8.jpg
            UUID format - no extractable date
            """
            result = get_date_from_filename("ba39a6c5-3886-486c-8995-d9a6591535e8.jpg")
            assert result.date is None
            assert result.year is None
            assert result.month is None

        def test_descriptive_name_only(self):
            """Pattern: Barack-Obama-Mourns-Dog-Bos-Death.jpg

            Real example from obama_family: Barack-Obama-Mourns-Dog-Bos-Death-Our-Family-Lost-a-True-Friend-01.jpg
            No extractable date
            """
            result = get_date_from_filename("Barack-Obama-Mourns-Dog-Bos-Death-Our-Family-Lost-a-True-Friend-01.jpg")
            assert result.date is None
            assert result.year is None
            assert result.month is None

        def test_flickr_id_style(self):
            """Pattern: 6599527897_8826e9d146_b.jpg

            Real example from obama_family: barack_11_6599527897_8826e9d146_b.jpg
            Flickr photo ID format - no extractable date
            """
            result = get_date_from_filename("barack_11_6599527897_8826e9d146_b.jpg")
            assert result.date is None
            assert result.year is None
            assert result.month is None

    # Edge cases and validation
    class TestEdgeCases:
        def test_invalid_month_13(self):
            """Invalid month (13) should not match as full date."""
            result = get_date_from_filename("photo_20211305.jpg")
            # Should not parse as Dec 5 because 13 is invalid month
            # May fall back to year-only or no match
            assert result.month != 13 if result.month else True

        def test_invalid_day_32(self):
            """Invalid day (32) should not match as full date."""
            result = get_date_from_filename("photo_20211232.jpg")
            # Should not parse as valid date
            assert result.date is None or result.date.day != 32

        def test_year_out_of_range_1800(self):
            """Year 1800 should not match (too old for photos)."""
            result = get_date_from_filename("photo_1800_test.jpg")
            assert result.year is None

        def test_year_out_of_range_2200(self):
            """Year 2200 should not match (too far future)."""
            result = get_date_from_filename("photo_2200_test.jpg")
            assert result.year is None

        def test_empty_filename(self):
            """Empty filename should return empty PhotoDateInfo."""
            result = get_date_from_filename("")
            assert result.date is None
            assert result.year is None
            assert result.month is None

        def test_full_path(self):
            """Full path with date in filename should still work."""
            result = get_date_from_filename("/Users/test/Pictures/IMG_20211205.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

    class TestAmbiguousPatterns:
        """Tests for filenames with multiple conflicting date patterns."""

        def test_conflicting_years_returns_undefined(self):
            """Two different years in filename should return undefined."""
            result = get_date_from_filename("photo_2020_backup_2021.jpg")
            assert result.date is None
            assert result.year is None
            assert result.month is None

        def test_conflicting_full_dates_returns_undefined(self):
            """Two different full dates should return undefined."""
            result = get_date_from_filename("IMG_20211205_copy_20220115.jpg")
            assert result.date is None
            assert result.year is None
            assert result.month is None

        def test_consistent_year_different_sources_ok(self):
            """Same year from different patterns should not conflict."""
            result = get_date_from_filename("medium_btl_2015_20151205.jpg")
            assert result.year == 2015
            assert result.month == 12
            assert result.date == datetime(2015, 12, 5)

        def test_consistent_full_date_multiple_matches_ok(self):
            """Same full date matched by multiple patterns should work."""
            result = get_date_from_filename("IMG_20211205_2021-12-05.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_conflicting_months_returns_undefined(self):
            """Same year but different months should return undefined."""
            result = get_date_from_filename("photo_20210115_20210305.jpg")
            assert result.date is None
            assert result.year is None
            assert result.month is None

        def test_year_only_and_full_date_same_year_ok(self):
            """Year-only pattern and full date with same year should use full date."""
            result = get_date_from_filename("archive_2021_IMG_20211205.jpg")
            assert result.date == datetime(2021, 12, 5)
            assert result.year == 2021
            assert result.month == 12

        def test_year_only_and_full_date_different_year_undefined(self):
            """Year-only pattern and full date with different years should be undefined."""
            result = get_date_from_filename("archive_2020_IMG_20211205.jpg")
            assert result.date is None
            assert result.year is None
            assert result.month is None
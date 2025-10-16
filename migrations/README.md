# Database Migrations

This folder contains SQL migration scripts for the photo organizer database.

## Running Migrations

To run a migration manually:

```bash
sqlite3 photo_organizer.db < migrations/001_add_face_locations.sql
```

## Migration List

- **001_add_face_locations.sql**: Adds location columns (top, right, bottom, left) to the faces table to store bounding box coordinates
- **002_add_location_and_tags.sql**: Adds GPS location fields (latitude, longitude, location_name) to photos table and creates tags table for EXIF metadata

## Notes

- Migrations are numbered sequentially (001, 002, etc.)
- Always backup your database before running migrations
- After adding location columns, you may need to re-index photos to populate location data for existing faces
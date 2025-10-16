-- Migration: Add location and tags to photos
-- Date: 2025-10-16
-- Description: Add GPS location fields to photos table and create tags table for EXIF metadata

-- Add location columns to photos table
ALTER TABLE photos ADD COLUMN latitude REAL;
ALTER TABLE photos ADD COLUMN longitude REAL;
ALTER TABLE photos ADD COLUMN location_name TEXT;

-- Create tags table
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id INTEGER NOT NULL,
    tag_name TEXT NOT NULL,
    tag_value TEXT,
    FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE
);

-- Create index on photo_id for faster lookups
CREATE INDEX IF NOT EXISTS idx_tags_photo_id ON tags(photo_id);

-- Create index on tag_name for faster searches
CREATE INDEX IF NOT EXISTS idx_tags_tag_name ON tags(tag_name);

-- Note: Existing photos will have NULL values for location columns
-- Re-index photos to populate location and tags data for existing photos
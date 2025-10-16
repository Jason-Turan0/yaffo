-- Migration: Add face location columns to faces table
-- Date: 2025-10-13
-- Description: Add bounding box coordinates (top, right, bottom, left) to store face locations

-- Add location columns
ALTER TABLE faces ADD COLUMN location_top INTEGER;
ALTER TABLE faces ADD COLUMN location_right INTEGER;
ALTER TABLE faces ADD COLUMN location_bottom INTEGER;
ALTER TABLE faces ADD COLUMN location_left INTEGER;

-- Note: Existing faces will have NULL values for these columns
-- Re-index photos to populate location data for existing faces
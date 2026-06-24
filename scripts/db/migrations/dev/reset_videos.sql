-- Reset all videos so the next index run re-processes them with the improved
-- video-face pipeline (lossless sample frames, min-size gate, quality-based crop
-- selection). Run against the app DB (dev: ~/Pictures/yaffo.db), then trigger an
-- index run from the Index Photos UI (or file_sync) -- the re-index clears each
-- item's old Face rows, unlinks their stale thumbnails, and overwrites the poster
-- in place, so this script only needs to flip the status.
--
-- "Needs indexing" == status != 'INDEXED' (see utils/index_jobs.py), so setting
-- the videos back to IMPORTED is what makes the pipeline pick them up again.

UPDATE media_items
SET status = 'IMPORTED'
WHERE media_type = 'video';

-- Show how many were reset.
SELECT changes() AS videos_reset;

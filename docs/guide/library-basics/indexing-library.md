# Indexing & Library Management

Indexing is how Yaffo learns what photos and videos are in your library. Yaffo
does not move your originals into a special folder. Instead, you choose media
folders, and Yaffo builds a local index from those files.

## What are you trying to do?

<div class="action-chooser" markdown="1">
<div class="action-choice" data-choice="Add folders" markdown="1">

### Add Media Folders

Open **Settings** and add one or more media directories. These are the folders
Yaffo scans for photos and videos.

Use folders that contain your actual library, such as:

- a Pictures folder;
- a camera import folder;
- an external drive folder;
- a folder synced from another device.

Yaffo stores its own database, thumbnails, logs, and temporary files separately
from these media folders. Removing a folder from Yaffo's settings removes it
from future scans; it does not delete the folder from disk.

</div>
<div class="action-choice" data-choice="Scan the library" markdown="1">

### Scan the Library

Go to **Library** → **Library Health**. Yaffo compares the configured media
folders with the local database.

The page shows several counts:

- **Total on Filesystem:** media files found in your configured folders.
- **Imported in Database:** files already known to Yaffo.
- **Indexed in Database:** files already processed for browsing and metadata.
- **Not Indexed:** files found on disk but not yet indexed.
- **Orphaned in DB:** database records whose files are no longer present.
- **Missing Thumbnails:** face crops and video posters Yaffo made that are gone
  from the thumbnail folder.

Scanning is a read-only comparison step. Under **Library status**, the page lists
what needs attention, one card per problem with its own fix, or says
**Everything is in sync** when nothing does. Select **Show files** on a card to
see the files it's about.

<div class="annotated-screenshot" markdown="1">

![The Library Health page showing library counts and an in-sync result](assets/indexing-library/library-health.webp)

<div class="screenshot-annotation" data-x="95.3" data-y="8.9" data-region="82.4 6.4 14.4 6.4" markdown="1">

#### Reindex Library

Rebuilds every indexed item, including detected faces. It removes face assignments, so Yaffo asks first.

</div>

<div class="screenshot-annotation" data-x="97.1" data-y="25.7" data-region="1.4 23.2 97.1 42" markdown="1">

#### Library Counts

Files on disk, imported and indexed items, items not yet indexed, orphaned entries, and missing thumbnails.

</div>

<div class="screenshot-annotation" data-x="97.1" data-y="73" data-region="1.4 70.5 97.1 26.6" markdown="1">

#### Library Status

One card per problem with its fix, or **Everything is in sync** when nothing needs attention.

</div>

</div>

#### Fix What the Scan Finds

Each card fixes only its own problem:

- **Photos aren't indexed:** files in your media folders that Yaffo doesn't know
  yet. Select **Index them** to import and process them in the background.
- **Library entries have no file:** entries whose file was deleted, or whose media
  folder was removed from Settings. Select **Remove them** to drop those entries.
  Their faces go with them, including any person assignments.
- **Thumbnails are missing:** face crops or video posters that show as broken
  images. Select **Regenerate thumbnails** to write them again from your photos and
  videos. Faces keep their people and ignored status; nothing is detected again.
- **Files couldn't be indexed:** see
  [Files That Couldn't Be Indexed](#files-that-couldnt-be-indexed).

If a media folder holds no files at all, a warning above the cards says so. That
usually means its drive isn't connected, and its photos are listed as having no
file. Reconnect the drive before you select **Remove them**. A similar warning
appears when the thumbnail folder isn't available; its count then shows a dash
instead of a number.

During indexing, Yaffo may:

- create thumbnails;
- read dates, camera metadata, and GPS metadata;
- detect faces;
- run automatic labels;
- prepare video posters or metadata;
- update searchable fields used by filters.

Large libraries can take time. You can keep using the app while background jobs
run.

</div>
<div class="action-choice" data-choice="New photos" markdown="1">

### Index the New Files

Open **Library** → **Library Health**, then select **Index them** on the
**Photos aren't indexed** card. This imports and processes files Yaffo does not
know yet.

- **People and faces:** existing face assignments stay.
- **Files on disk:** originals stay in place.
- **Scope:** new, unindexed files.

See [Scan the Library](#scan-the-library) and [Watch Background Jobs](#watch-background-jobs).

</div>
<div class="action-choice" data-choice="Broken thumbnails" markdown="1">

### Regenerate Missing Thumbnails

Select **Regenerate thumbnails** on **Library Health** to recreate missing face
crops and video posters from the originals.

- **People and faces:** assignments and ignored status stay; faces are not detected again.
- **Files on disk:** originals stay in place.
- **Scope:** missing thumbnails.

See [Fix What the Scan Finds](#fix-what-the-scan-finds).

</div>
<div class="action-choice" data-choice="Changed metadata" markdown="1">

### Reindex the Affected Items

Use **Reindex** in a photo's details panel for one item, or **Reindex Library**
on Library Health when the whole library needs rebuilding. Reindexing rereads
metadata and rebuilds thumbnails, labels, and detected faces.

- **People and faces:** rebuilt items lose their face-to-person assignments.
- **Files on disk:** originals stay in place.
- **Scope:** one item, or every indexed file with **Reindex Library**.

!!! warning "Review the scope before reindexing"
    **Reindex Library** removes existing face assignments throughout the library.
    If only thumbnails are broken, use **Regenerate thumbnails** instead.

See [Re-Index After Changes](#re-index-after-changes).

#### Re-Index After Changes

Yaffo has two automatic ways to notice library changes while the app is running:

- A watcher process monitors configured media directories and reacts when files
  are added, modified, moved, or removed.
- The built-in **File sync** automation runs at the start of every hour. It finds
  new paths and orphaned database records, like the scan on Library Health.

These automatic checks are useful for normal day-to-day changes, such as copying
new photos into a watched folder.

You can still open **Library Health** whenever you want an immediate check. It's
useful when you:

- add new photos or videos to a configured folder;
- remove files from a configured folder;
- add another media directory;
- move a library folder;
- want Yaffo to clean up orphaned database records;
- see broken face or poster images.

Use **Reindex Library** when files were modified in place while Yaffo was not
running, or when an indexing change requires Yaffo to rebuild derived data for
items it already knows. Reindexing rereads every indexed file and rebuilds its
metadata, thumbnails, labels, and detected faces. Because faces are detected
again, all existing face-to-person assignments are removed. Yaffo asks you to
confirm before starting the job. If only thumbnails are broken, use
**Regenerate thumbnails** instead: it keeps every assignment.

Some operations, such as changing the automatic label vocabulary, have their own
reprocessing controls. Use Library Health for file-system changes or when you do
not want to wait for the watcher or hourly sync.

</div>
<div class="action-choice" data-choice="Missing originals" markdown="1">

### Check the Drive Before Removing Entries

Reconnect the drive and confirm you can open the original files in Finder or
File Explorer. Then scan **Library Health** again. An empty mount point can make
valid library entries appear to have no file.

- **People and faces:** **Remove them** drops the missing entries and their associated faces, including assignments.
- **Files on disk:** removing an index entry does not reconnect a drive.
- **Scope:** database entries whose originals are no longer present.

!!! warning "Verify the originals first"
    Leave **Remove them** alone while a drive is disconnected or unexpectedly empty.

See [External Drive Not Showing](../reference-maintenance/troubleshooting.md#external-drive-not-showing).

</div>
<div class="action-choice" data-choice="Background jobs" markdown="1">

### Watch Background Jobs

The **Library Health** page shows the import, index, or thumbnail run in
progress as a job card, with its progress, any error count, and **Cancel** when cancellation is
available. After you cancel, the run shows **Stopping** until it finishes the
item it's working on, then **Cancelled**. Finished runs move to the page's
**Run history**, each with a status chip: **Completed**, **Completed with
errors** when some files failed, **Cancelled**, or **Failed**. A thumbnail
run's row also says how many thumbnails it regenerated. A run that is
still in progress has **Cancel** in the run history too, and a run's technical
error, when it has one, is under **Details**.

A failed run, or one with errors, has an **Ask Yaffo** button that opens the
[assistant](../start-here/ask-yaffo.md) with that run attached, so it can look up
what went wrong.

If you close the browser tab, the app and its background worker can continue
running as long as Yaffo itself is still running. Use the Yaffo tray/menu icon to
reopen the app or quit it.

</div>
<div class="action-choice" data-choice="Indexing errors" markdown="1">

### Files That Couldn't Be Indexed

Occasionally a file can't be indexed, for example because it's damaged, cut off
partway through, or in a format Yaffo can't decode. The file still appears in your
library and you can open it, but it has no date, location, faces, or labels,
because those are read during indexing. Undated files sort with other undated
items rather than near when they were taken.

When this happens:

- **Library Health** shows a card listing the files that couldn't be indexed,
  with the reason for each under **Show files**. Select a file name to open it.
- The photo's details panel explains why, with the technical error under
  **Details**.
- File sync leaves these files alone, so they aren't retried every hour. A file is
  tried again automatically only when it changes on disk, for example after you
  repair or replace it.

To try again yourself, select **Retry all** on Library Health, or **Reindex** in
the photo's details panel. Retrying is also worthwhile after updating Yaffo, since
a newer version may handle the file.

If a file can't be reached at all, for example because its drive was disconnected
during indexing, it isn't marked as failed. The next sync tries it again.

</div>
<div class="action-choice" data-choice="Supported files" markdown="1">

### Supported Media

Yaffo indexes these file extensions:

- **Photos:** `.jpg`, `.jpeg`, `.png`, and `.heic`.
- **Videos:** `.mp4`, `.mov`, `.m4v`, `.avi`, `.mkv`, `.wmv`, and `.flv`.

MP4, MOV, and M4V videos can play inline when their codec is supported by the
browser. Yaffo still indexes the other video containers for metadata, posters,
and faces, but opens them in an external application instead of playing them in
the detail view.

If a file does not appear after indexing, check that:

- the folder is configured in Settings;
- the file is inside that folder;
- the file extension is a supported media type;
- Yaffo has permission to read the folder;
- indexing has finished.

</div>
</div>

# External Drive Not Showing

If a media directory lives on an external drive, Yaffo can only index it while
the drive is connected, mounted at the expected path, and readable.

1. Check that the drive is visible in your operating system and that you can open
   the folder containing the original photos. A mount point that exists but is
   empty does not mean the library is available.
2. In **Settings** → **Media Directories**, confirm that the configured path still
   points to that folder. Remount or reconnect the drive before changing the
   library configuration.
3. If the folder responds slowly or file listings fail, check the cable, power,
   and drive health before starting another scan. Keep a backup of the originals.
4. Once the files are visible again, use **Utilities** → **Index Photos** to compare
   the filesystem and database. Only run **Sync Database** after the expected
   files appear in the scan.

An unattended File sync run leaves indexed items under an unexpectedly empty
media folder alone. A manual sync can remove missing items from Yaffo's index, so
verify the drive before confirming it. Removing an item from the index does not
repair or reconnect the drive.

If **Ask Yaffo** is available, ask it to check media-folder availability. Its
diagnostic result can distinguish a missing folder, an empty mount point, a slow
listing, and a permissions problem.

See [Indexing & Library Management](../../library-basics/indexing-library.md) and
[Settings Reference](../settings.md#media-directories).

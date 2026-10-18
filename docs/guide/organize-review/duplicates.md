# Finding Duplicates

The duplicate utility finds photos and videos with the same visual fingerprint.
Use it as a review workflow, not as an automatic delete button.

## Explore the Page

<div class="annotated-screenshot" markdown="1">

![The duplicate scan configuration with one four-file sample directory](assets/duplicates/utilities-remove-duplicates.webp)

<div class="screenshot-annotation" data-x="97.1" data-y="6.7" data-region="1.4 4.2 97.1 26" markdown="1">

### Scan Totals

How many media files and directories the scan will cover.

</div>

<div class="screenshot-annotation" data-x="94.8" data-y="64.3" data-region="3.7 61.8 92.7 14" markdown="1">

### Directory

A folder to scan. Type a path or **Browse** to one; **Delete** removes it from the scan.

</div>

<div class="screenshot-annotation" data-x="33.4" data-y="82.5" data-region="3.7 80 31.3 9.4" markdown="1">

### Rescan and Add

**Rescan Directories** refreshes the media count. **+ Add Another Directory** adds a folder.

</div>

<div class="screenshot-annotation" data-x="46" data-y="82.5" data-region="35.3 80 12.2 9.4" markdown="1">

### Find Duplicates

Starts the scan as a background job. [Read about starting a scan](#start-a-duplicate-scan).

</div>

</div>

## How Duplicate Detection Works

Yaffo scans the selected directories recursively and calculates a perceptual hash
from each photo. For a video, it hashes an indexed poster or a frame extracted
from the video. Files with the same media type and hash are grouped together,
even when their filenames or metadata differ.

Because visual comparison is not the same as human judgment, always review the
results before removing anything.

## Start a Duplicate Scan

Go to **Library** → **Remove Duplicates**.

1. Click **+ Add Another Directory**.
2. Type a directory path or click **Browse** to select one.
3. Add more directories if needed, then click **Rescan Directories** to refresh
   the media count.
4. Click **Find Duplicates**.

Yaffo starts a background job. You can leave the page while it runs. When the job
finishes, click **Show Results** on its job card.

## Review Duplicate Groups

Duplicate results are shown in groups. Each group contains files with the same
visual fingerprint.

<div class="annotated-screenshot" markdown="1">

![Duplicate results with two groups and one selected copy in each](assets/duplicates/duplicate-results.webp)

<div class="screenshot-annotation" data-x="97.1" data-y="13.1" data-region="1.4 10.6 97.1 8.2" markdown="1">

### Result Totals

Media processed, duplicate groups found, and how many copies are selected.

</div>

<div class="screenshot-annotation" data-x="94.8" data-y="25.5" data-region="3.7 23.3 92.7 4.5" markdown="1">

### Action

What happens to the selected copies. [Read about the actions](#choose-an-action).

</div>

<div class="screenshot-annotation" data-x="21.7" data-y="30.9" data-region="3.7 29.4 19.6 2.9" markdown="1">

### Remove Button

**Remove Selected Duplicates** starts the action immediately, with no extra confirmation. [Read more](#remove-selected-duplicates).

</div>

<div class="screenshot-annotation" data-x="32.2" data-y="44.9" data-region="3.7 42.4 30 20.9" markdown="1">

### Keeper

The first card in each group starts unselected as the copy to keep. This is a starting point, not a quality judgment.

</div>

<div class="screenshot-annotation" data-x="63.5" data-y="44.9" data-region="35 42.4 30 20.9" markdown="1">

### Selected Copy

A blue outline marks a copy selected for the action. Click a card to toggle it.

</div>

</div>

Yaffo leaves the first card in each group unselected as the initial keeper and
selects the remaining cards for removal. This is only a starting point; it is not
a quality judgment. Click any card to toggle its selected outline.

The results currently show previews but not filenames, paths, or capture dates.
Compare the visible content carefully. If you cannot confidently identify the
keeper, leave that group alone and repeat the scan with a narrower directory.

For each group, compare:

- the image or video preview;
- cropping and orientation;
- visible resolution or compression differences;
- whether one copy is clearly more complete.

Select only the files you want Yaffo to act on. Leave the keeper unselected.

## Choose an Action

The results page supports several actions:

- **Move to Trash:** sends selected files to the operating system trash when
  possible. This is the safest cleanup option.
- **Move to Folder:** moves selected files to a folder you choose. If a filename
  already exists there, Yaffo adds a numeric suffix. This is useful for quarantine
  or manual review.
- **Permanently Delete:** deletes selected files directly.

Prefer **Move to Trash** or **Move to Folder** until you trust the results for
your library.

## Remove Selected Duplicates

After selecting duplicates and choosing an action, click **Remove Selected
Duplicates**. The action starts immediately; there is currently no additional
confirmation dialog.

Yaffo starts a background job for the removal action. The original duplicate scan
result is replaced by the removal job once processing starts.

## Safety Checklist

Before removing files:

- make sure every selected file is truly a duplicate;
- make sure at least one good copy remains unselected in each group;
- prefer **Move to Trash** or **Move to Folder** for the first few runs;
- avoid **Permanently Delete** unless you have backups;
- remember that **Remove Selected Duplicates** starts immediately;
- run a small scan first if you are testing the workflow.

## When No Duplicates Are Found

If Yaffo reports **No Duplicates Found**, the scan completed without finding two
readable files of the same media type with the same visual fingerprint. Try a
broader directory or verify that the files are readable. Files do not need to be
indexed before this utility can scan them.

## Review Results With Ask Yaffo

[Ask Yaffo](../start-here/ask-yaffo.md) can read a finished scan's results and
suggest which copy in each group to keep, such as the higher-resolution copy or the
one with faces and tags. It proposes moving the others to the system trash or into
a folder for review, as a change you approve. Moving files is off until you turn
it on in **Settings** → **Assistant**.

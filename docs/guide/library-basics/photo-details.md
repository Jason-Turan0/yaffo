---
hide:
  - toc
---

# Photo Details

The photo detail page is where you review one photo or video closely. Open it by
clicking a card in the gallery.

## Explore the Page

<div class="annotated-screenshot" markdown="1">

![A photo detail page with file information, location, people, detected faces, and labels](assets/photo-details/media-detail.webp)

<div class="screenshot-annotation" data-x="25" data-y="12" data-region="2.8 9 23 26" markdown="1">

### File Information and Actions

Check which original file you are viewing, its capture date, and its device.
**Open File** and **Open Folder** use your operating system. **Reindex** rebuilds
this item's derived data and removes its face-to-person assignments.

[Read about file information](#read-file-information) and
[reindexing one item](#reindex-one-item).

</div>

<div class="screenshot-annotation" data-x="25" data-y="39" data-region="2.8 36 23 16" markdown="1">

### Location

Review the assigned location name and GPS coordinates. **View on Map** opens the
location view. A location name and GPS coordinates are separate pieces of data.

[Read about location information](#review-location-information).

</div>

<div class="screenshot-annotation" data-x="25" data-y="58" data-region="2.8 52 23 22" markdown="1">

### People and Faces

People are linked through assigned faces. Hover over a face thumbnail to
highlight it in the photo, or click the thumbnail to reassign it.

[Read about people and faces](#review-people-and-faces).

</div>

<div class="screenshot-annotation" data-x="25" data-y="80" data-region="2.8 74 23 17" markdown="1">

### Labels and Tags

Labels are automatic search hints managed through the vocabulary in Settings.
Tags are your editable name/value pairs; use **Edit Tags** to change them.

[Read about labels](#review-labels) and [editing tags](#edit-tags).

</div>

<div class="screenshot-annotation" data-x="95" data-y="4" data-region="92 1.8 5.5 5.5" markdown="1">

### Favorite

Use the heart to mark a photo for quick retrieval with the Favorites filter.
Favorite status is stored in Yaffo and can also be exported to the file when
that automation option is enabled.

[Read about favorites](#mark-a-favorite).

</div>

</div>

## Review the Preview

The main area shows the selected photo or video.

For photos, Yaffo shows the image and can draw face highlights when you hover
over detected face thumbnails in the sidebar.

For videos, Yaffo shows an in-browser player when the format is playable. If the
video format cannot play in the browser, Yaffo shows the poster image and offers
an **Open in default player** action.

## Read File Information

The **File Information** section shows the file name and folder. When available,
it also shows:

- date taken;
- camera or device;
- video duration;
- video resolution;
- video codec.

Use this section when you need to confirm which original file you are looking
at.

## Open the Original File or Folder

Use **Open File** to open the original media file with your operating system's
default app.

Use **Open Folder** to open the folder that contains the original file.

These actions use your local operating system. If the file or folder no longer
exists, Yaffo shows an error instead of opening it.

## Reindex One Item

Use **Reindex** when the selected file's metadata, thumbnail, labels, or detected
faces need to be rebuilt. Reindexing rereads the original file and detects its
faces again. Existing face-to-person assignments for this item are removed, so
Yaffo asks you to confirm before starting the background job.

## Mark a Favorite

Use the heart button on the preview to toggle favorite status. Favorites can be
used later in gallery filters.

Favorite status is stored in Yaffo's database. If the **Export photo tag**
automation's favorite option is enabled, Yaffo also writes a `Favorite` keyword
to the file.

## Review Location Information

The **Location** section shows a location name when one has been assigned. If the
photo has GPS coordinates, it also shows the coordinates and a **View on Map**
link.

If the page says **No location information**, Yaffo does not have GPS coordinates
or a location name for that media item.

Use the [Locations & Map](../organize-review/locations.md) guide for assigning
or clearing location names in bulk.

## Review People and Faces

The **People** section lists people assigned to faces in the photo. Click a
person to open that person's face page.

The **Faces** section shows detected face thumbnails. Hover over a face thumbnail
to highlight that face in the main photo when face coordinates are available.
Click a face thumbnail to open the reassignment control, then choose a person and
apply the change.

Use the [Faces & People](../organize-review/faces-and-people.md) guide for
assigning, correcting, and reviewing people.

## Review Labels

The **Labels** section shows automatic classification labels that Yaffo assigned
to the media item. Label chips include confidence information in their tooltip.

Labels are read-only on this page. Manage the label vocabulary and reclassify
photos from Settings. See [Labels and Auto-Classification](../organize-review/labels.md).

## Edit Tags

The **Tags** section shows user-editable tags.

Click **Edit Tags** to open the tag editor. In the editor, you can:

- add a tag name and optional value;
- edit existing tag names or values;
- remove tags;
- save all tag changes at once.

Every tag must have a name. Values are optional.

## Keyboard Shortcut

Press **Escape** to go back when the detail page was opened from another Yaffo
page.

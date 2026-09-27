# Concepts and Glossary

This page defines the terms used throughout the Yaffo guide.

## Library

**Library**

The collection of photos and videos Yaffo has indexed, together with the
metadata it records about them. The library points to your originals; it does
not replace or relocate them.

**Media item**  
A photo or video that Yaffo knows about. Media items come from the folders you
add in Settings.

**Media directory**  
A folder Yaffo scans for photos and videos. Yaffo does not require your media to
live inside its own data folder; it indexes the folders you choose.

**Index / indexed**

The index is Yaffo's local record of the files in your media directories and the
metadata it has processed for them. A media item is indexed when that record is
ready to appear in the library. Indexing can include thumbnail generation,
metadata extraction, face detection, automatic labels, and location data.

**Thumbnail**  
A smaller preview image Yaffo creates for fast browsing. Thumbnails are stored in
Yaffo's app data, not next to your original photos.

**Orphaned item**  
A database record for a file that is no longer found under the configured media
folders. The indexing utility lists these with the reason it recorded: the file
was deleted from disk, or its media directory was removed from Settings.
Syncing removes the entries.

**Failed item**  
A media item Yaffo could not index, for example because the file is damaged or
in a format it cannot decode. The item still appears in the library and opens
normally, but it has no date, location, faces, or labels, because those are read
during indexing. Its details panel explains the failure, **Retry all** on Index
Photos tries the listed files again, and **Reindex** in the details panel retries
one item. Yaffo leaves a failed file alone until it changes on disk.

## Organization

**Tag**  
A user-editable name/value pair on a media item. Tags are useful for custom
organization that does not fit into people, labels, or locations.

**Label**  
A machine-assisted category such as `beach`, `dog`, or `wedding`. Labels come
from the classification vocabulary in Settings and can be used in filters.

**Person**  
A named person in your library. People are connected to photos through assigned
faces.

**Face**  
A detected face crop from a photo. You can assign faces to people, remove
incorrect assignments, and use people in gallery filters.

**Location name**  
A human-readable name for a GPS-backed photo location, such as `The White House`
or `Grant Park`. A photo may have GPS coordinates without having a location name
yet.

**Favorite**  
A simple marker for photos or videos you want to find again quickly.

**Album**

A collection you curate by hand. Albums keep chosen media items together in
your preferred order and can be shared with another Yaffo device.

## Workflows

**Background job**  
A task Yaffo runs outside the current page interaction, such as indexing,
reclassifying labels, generating a page, or scanning for duplicates.

**Run**  
One execution of a background job or an automation. While it is going, it
appears as a job card with its progress; when it finishes, it moves to the
page's **Run history** with the status it ended in, its result, and - when
something went wrong - its **Details** and an **Ask Yaffo** button. **Cancel**
stops a run that is still going, from its card or from its run history row.

**Duplicate group**  
A set of photos Yaffo believes are duplicates or near-duplicates. You review the
group before deciding what to keep or remove.

**Automation**  
A scheduled or event-driven behavior. Some automations are built into Yaffo;
others can be created for your own workflows. Each automation runs on one or more
triggers: a schedule, or an event it responds to.

**Custom page**  
A page built from your photo library, often with AI-generated widgets. Custom
pages can be used for dashboards, albums, summaries, or experiments.

**Widget**  
A single interactive part of a custom page.

**Theme**  
A visual style for Yaffo's interface.

**Ask Yaffo**  
The built-in assistant. It answers from Yaffo's documentation, checks this
computer when troubleshooting, and proposes changes to your library. See
[Ask Yaffo](ask-yaffo.md).

**Change card**  
A change the assistant proposes, shown with the exact number of items it
affects. Nothing changes until you approve it, and most changes can be undone.

## Sharing

**Sharing**  
Pairing two Yaffo installations so one can copy selected parts of the other's
library. Sharing copies media; it never moves or deletes your originals.

**Paired device**  
Another Yaffo installation you have paired with this one. Pairing lets the two
copies recognize each other, but on its own it gives the other device access to
nothing.

**Share**  
An authorization that lets a paired device pull one specific part of your
library: a media directory, a folder inside one, or an album. You can revoke a
share at any time; files the other device already copied are kept.

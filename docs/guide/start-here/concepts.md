# Concepts and Glossary

This page defines the terms used throughout the Yaffo guide.

## Library

**Media item**  
A photo or video that Yaffo knows about. Media items come from the folders you
add in Settings.

**Media directory**  
A folder Yaffo scans for photos and videos. Yaffo does not require your media to
live inside its own data folder; it indexes the folders you choose.

**Indexed**  
A media item is indexed when Yaffo has recorded it in the local database and
processed enough information to show it in the library. Indexing can include
thumbnail generation, metadata extraction, face detection, automatic labels, and
location data.

**Thumbnail**  
A smaller preview image Yaffo creates for fast browsing. Thumbnails are stored in
Yaffo's app data, not next to your original photos.

**Orphaned item**  
A database record for a file that is no longer found in the configured media
folders. The indexing utility can show these so you can clean up the library
index.

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

## Workflows

**Background job**  
A task Yaffo runs outside the current page interaction, such as indexing,
reclassifying labels, generating a page, or scanning for duplicates.

**Duplicate group**  
A set of photos Yaffo believes are duplicates or near-duplicates. You review the
group before deciding what to keep or remove.

**Automation**  
A scheduled or event-driven behavior. Some automations are built into Yaffo;
others can be created for your own workflows.

**Custom page**  
A page built from your photo library, often with AI-generated widgets. Custom
pages can be used for dashboards, albums, summaries, or experiments.

**Widget**  
A single interactive part of a custom page.

**Theme**  
A visual style for Yaffo's interface.

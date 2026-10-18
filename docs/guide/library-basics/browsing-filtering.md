# Browsing, Filtering, and Search

The **Home** page is the main photo library view. It shows indexed photos and
videos as a grid, with filters on the left.

## Explore the Page

<div class="annotated-screenshot" markdown="1">

![Photo library](assets/browsing-filtering/gallery-home.webp)

<div class="screenshot-annotation" data-x="10" data-y="5.8" data-region="1.5 3 18.5 96" markdown="1">

### Filter Sidebar

Narrows the gallery to matching media. [Read about filters](#use-the-filter-sidebar).

</div>

<div class="screenshot-annotation" data-x="37" data-y="10.5" data-region="24 9.3 11.5 2.5" markdown="1">

### Photo Count

How many photos match out of your whole library.

</div>

<div class="screenshot-annotation" data-x="86" data-y="8" data-region="88 6.3 9.6 3.7" markdown="1">

### Grid and Timeline

Switch between the photo grid and the timeline view.

</div>

<div class="screenshot-annotation" data-x="39.5" data-y="18.5" data-region="22.7 16.5 18.3 39" markdown="1">

### Media Card

Shows the date and any people assigned to the photo. Videos show a play badge and their duration. Click a card to open its detail page.

</div>

</div>

## Browse the Gallery

Each card represents a photo or video in your indexed library. The gallery is
ordered by date, with the newest media first.

Use the gallery to:

- scan recent photos quickly;
- open a photo or video detail page;
- spot videos by their video card controls;
- review dates and visible people at a glance;
- move through large libraries with pagination.

Click a card to open the media detail page.

## Use the Filter Sidebar

The filter sidebar narrows the gallery to matching media.

<div class="annotated-screenshot annotated-screenshot--narrow" markdown="1">

![Gallery filter sidebar](assets/browsing-filtering/gallery-filter-sidebar.webp)

<div class="screenshot-annotation" data-x="92" data-y="4.2" data-region="81 3.2 7 2.2" markdown="1">

### Sidebar Settings

Choose which filter groups are visible. [Read about configuring the sidebar](#configure-the-sidebar).

</div>

<div class="screenshot-annotation" data-x="92" data-y="8" data-region="11.5 5.5 77 10" markdown="1">

### Year and Month

Find media from a time period.

</div>

<div class="screenshot-annotation" data-x="92" data-y="52" data-region="11.5 50.5 77 8" markdown="1">

### Location Filters

Filter by assigned location names, or find photos that still need one.

</div>

<div class="screenshot-annotation" data-x="92" data-y="61.5" data-region="11.5 60 77 7.5" markdown="1">

### Proximity Search

Find photos within a distance of a place.

</div>

<div class="screenshot-annotation" data-x="92" data-y="93" data-region="11.5 91.3 77 6.2" markdown="1">

### Apply or Clear

**Apply Filters** updates the gallery. **Clear Filters** returns to the unfiltered gallery.

</div>

</div>

Common filters include:

- **Year** and **Month:** find media from a time period.
- **File:** search by file or folder name.
- **People:** show photos containing one or more people.
- **Gender:** filter by detected or assigned face gender metadata when present.
- **Label:** filter by automatic classification labels.
- **Tag Name** and **Tag Value:** filter by custom tags.
- **Locations:** filter by assigned location names.
- **Only show photos without location names:** find GPS-backed photos that still
  need a location name.
- **Proximity Search:** find photos near a place.
- **Device:** filter by camera or device metadata.
- **Favorites:** show only favorited media.
- **Media Type:** show photos only or videos only.
- **Shape:** filter by orientation — portrait, landscape, or square.

Click **Apply Filters** to update the gallery.

## Match Any or All

Some filter groups let you choose how multiple selected values are matched.

Use **any** when a photo can match at least one selected value. For example,
photos containing Alice or Bob.

Use **all** when a photo must match every selected value. For example, photos
containing both Alice and Bob.

Not every filter benefits from **all** matching. A photo has only one assigned
location name, so selecting all locations is treated like any matching.

## Clear Filters

Click **Clear Filters** to return to the unfiltered gallery.

If a filter result looks empty, clear filters first. Then reapply one filter at a
time to see which condition narrowed the results.

## Configure the Sidebar

Click the filter configuration button in the sidebar header to choose which
filter groups are visible. This is useful if you mostly use a smaller set of
filters.

Filter layouts are page-specific. Changing the gallery sidebar does not
necessarily change the Locations map sidebar.

## Useful Searches

Try these common patterns:

- **Find videos:** set **Media Type** to **Videos Only**.
- **Find photos from a year:** choose a **Year**, then apply filters.
- **Find unnamed locations:** enable **Only show photos without location names**.
- **Find photos from one camera:** choose a **Device**.
- **Find classified photos:** choose one or more **Label** values.
- **Find custom organization:** choose a **Tag Name**, then a **Tag Value**.

## When Results Look Wrong

If expected photos are missing, follow
[Photos Do Not Appear](../reference-maintenance/troubleshooting.md#photos-do-not-appear)
in Troubleshooting. It checks filters, media folders, drives, and indexing in turn.

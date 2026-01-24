# Test Implementation Plan

## Test Files to Generate

### 1. photo-details-file-info.spec.ts
- Navigate to /photo/view/14
- Verify page loads (status 200)
- Check file name "DSCN0010.jpg" visible
- Check folder path visible
- Check main image loads (GET /photos/14 returns 200)
- Check "Open File" and "Open Folder" buttons visible

### 2. photo-details-people-faces.spec.ts
- Navigate to /photo/view/14
- Verify People section shows count
- Check person links are clickable
- Verify Faces section shows count
- Check face thumbnails load correctly
- Test hover on face thumbnail highlights it on main image

### 3. photo-details-tags.spec.ts
- Navigate to /photo/view/14
- Click "Edit Tags" button
- Verify modal #tagsModal opens
- Add new tag: name="TestTag", value="TestValue"
- Click Save Changes button
- Verify new tag appears in Tags section
- Cleanup: remove the added tag

### 4. photo-details-location.spec.ts
- Navigate to /photo/view/14 (has location data per spec)
- Verify Location section shows coordinates
- Check "View on Map" link exists
- Verify link contains correct coordinates
- Verify link target is _blank (opens new tab)

### 5. photo-details-face-hover.spec.ts
- Navigate to /photo/view/14
- Hover over face thumbnail
- Verify #faceCanvas becomes visible/active
- Verify face highlight appears on canvas
- Mouse away
- Verify highlight clears

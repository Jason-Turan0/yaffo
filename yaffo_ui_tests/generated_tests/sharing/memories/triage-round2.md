# Round 2 Triage: Cascading Failures

## New failures after fixing sharing_revoke_a_device:
1. sharing_gallery_without_a_download_directory - line 274: `.remote-notice` not found
2. sharing_grant_an_album - line 437: expects 4 photos, got 3
3. sharing_album_share_modal_toggle - line 474: checkbox already checked (expected unchecked)
4. sharing_revoke_a_grant - line 522: share count 1 (expected 0 after revoke)

## Analysis
- Test 1 (gallery without download dir): B may have a download directory set by peer seed now
- Tests 2-4 are cascading: album only has 3 photos → modal toggle assumes unchecked but it's checked → revoke can't clear album share
- The album has 3 photos because sharing_album_share_modal_toggle from a prior state left it that way, OR the seeded album always had 3 members

## Next steps
1. Check the files.html template for how remote-notice is rendered
2. Check if the download directory precondition changed
3. Verify seeded album count

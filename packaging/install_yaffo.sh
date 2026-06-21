#!/bin/bash
# Authorize an ad-hoc-signed (un-notarized) Yaffo build so Gatekeeper will run it.
#
#   ./packaging/install_yaffo.sh ["/path/to/Yaffo Photo Organizer.app"]
#
# Defaults to /Applications. Drag the app out of the DMG to /Applications first,
# then run this. Re-applies the ad-hoc signature (in case copying disturbed it)
# and removes the quarantine flag macOS sets on downloaded apps.
set -euo pipefail

APP="${1:-/Applications/Yaffo Photo Organizer.app}"

if [ ! -d "$APP" ]; then
    echo "Not found: $APP"
    echo "Usage: $0 [/path/to/Yaffo Photo Organizer.app]"
    exit 1
fi

echo "==> Re-applying ad-hoc signature"
codesign --force --deep --sign - "$APP"

echo "==> Removing quarantine attribute"
xattr -dr com.apple.quarantine "$APP" || true

echo "Authorized: $APP"
echo "Launch it from Applications (or: open \"$APP\")."

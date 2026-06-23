#!/bin/bash
# Build the macOS .app + .dmg for Yaffo with PyInstaller, ad-hoc signed (no
# Developer ID needed).
#
#   ./packaging/build_dmg.sh
#
# Run from an activated venv that has the dev deps (pyinstaller). Downloads the
# bundled assets (exiftool + ML models) first, then freezes the app and wraps it in
# a DMG. The .dmg lands in dist/. After installing, run packaging/install_yaffo.sh.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="python"
[ -x "venv/bin/python" ] && PYTHON="venv/bin/python"
PYINSTALLER="pyinstaller"
[ -x "venv/bin/pyinstaller" ] && PYINSTALLER="venv/bin/pyinstaller"

APP="dist/Yaffo Photo Organizer.app"

echo "==> Downloading bundled assets (exiftool, ffmpeg, models)"
"$PYTHON" packaging/download_assets.py

echo "==> Staging attribution file into resources/"
cp THIRD_PARTY_LICENSES.txt resources/THIRD_PARTY_LICENSES.txt

echo "==> Bumping version + stamping build info"
"$PYTHON" packaging/bump_version.py

# Name the DMG after the just-bumped version.
DMG="dist/Yaffo Photo Organizer-$(cat VERSION).dmg"

echo "==> Generating app + menu-bar icons"
./packaging/make_icons.sh

echo "==> PyInstaller freeze"
"$PYINSTALLER" --noconfirm yaffo.spec

echo "==> Ad-hoc signing (.app + nested binaries)"
codesign --force --deep --sign - "$APP"

echo "==> Building DMG (/Applications shortcut + volume icon)"
rm -f "$DMG"
STAGING="$(mktemp -d)"
ditto "$APP" "$STAGING/Yaffo Photo Organizer.app"   # ditto preserves the signature
ln -s /Applications "$STAGING/Applications"
[ -f packaging/icon.icns ] && cp packaging/icon.icns "$STAGING/.VolumeIcon.icns"

# Build read-write, set the volume's custom-icon flag, then compress to the final DMG.
RW="$(mktemp -u).dmg"
hdiutil create -volname "Yaffo Photo Organizer" -srcfolder "$STAGING" -fs HFS+ -format UDRW -ov "$RW" >/dev/null
MNT="$(mktemp -d)"
hdiutil attach "$RW" -mountpoint "$MNT" -nobrowse >/dev/null
[ -f "$MNT/.VolumeIcon.icns" ] && SetFile -a C "$MNT" || true
hdiutil detach "$MNT" >/dev/null
hdiutil convert "$RW" -format UDZO -o "$DMG" >/dev/null
rm -f "$RW"; rm -rf "$STAGING" "$MNT"

echo
echo "Done. DMG: $DMG"
echo "Install: open the DMG, drag the app to Applications, then:"
echo "  ./packaging/install_yaffo.sh"

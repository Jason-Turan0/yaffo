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
DMG="dist/Yaffo Photo Organizer-0.0.1.dmg"

echo "==> Downloading bundled assets (exiftool, models)"
"$PYTHON" packaging/download_assets.py

echo "==> Staging attribution file into resources/"
cp THIRD_PARTY_LICENSES.txt resources/THIRD_PARTY_LICENSES.txt

echo "==> PyInstaller freeze"
"$PYINSTALLER" --noconfirm yaffo.spec

echo "==> Ad-hoc signing (.app + nested binaries)"
codesign --force --deep --sign - "$APP"

echo "==> Building DMG (with /Applications shortcut)"
rm -f "$DMG"
STAGING="$(mktemp -d)"
ditto "$APP" "$STAGING/Yaffo Photo Organizer.app"   # ditto preserves the signature
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "Yaffo Photo Organizer" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"

echo
echo "Done. DMG: $DMG"
echo "Install: open the DMG, drag the app to Applications, then:"
echo "  ./packaging/install_yaffo.sh"

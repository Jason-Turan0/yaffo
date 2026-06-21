#!/bin/bash
# Generate the macOS app icon (.icns) and the menu-bar icon (PNG) from the app's
# SVG favicon. Run by build_dmg.sh before PyInstaller. Outputs are gitignored
# (regenerated each build from the committed SVG, the single source of truth).
set -euo pipefail

cd "$(dirname "$0")/.."

SVG="yaffo/static/themes/classic/favicon.svg"
OUT_ICNS="packaging/icon.icns"
OUT_MENU="resources/branding/menubar.png"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Rasterize the vector at full size (sips can't read SVG; qlmanage renders it).
qlmanage -t -s 1024 -o "$TMP" "$SVG" >/dev/null 2>&1
MASTER="$TMP/$(basename "$SVG").png"
[ -f "$MASTER" ] || { echo "make_icons: failed to render $SVG"; exit 1; }

# .icns: build the iconset (the sizes Apple expects) and pack it.
ICONSET="$TMP/icon.iconset"
mkdir -p "$ICONSET"
gen() { sips -z "$2" "$2" "$MASTER" --out "$ICONSET/$1" >/dev/null; }
gen icon_16x16.png 16
gen icon_16x16@2x.png 32
gen icon_32x32.png 32
gen icon_32x32@2x.png 64
gen icon_128x128.png 128
gen icon_128x128@2x.png 256
gen icon_256x256.png 256
gen icon_256x256@2x.png 512
gen icon_512x512.png 512
gen icon_512x512@2x.png 1024
iconutil -c icns "$ICONSET" -o "$OUT_ICNS"

# Menu-bar icon: 44px (≈22pt @2x). rumps scales it to the bar height.
mkdir -p "$(dirname "$OUT_MENU")"
sips -z 44 44 "$MASTER" --out "$OUT_MENU" >/dev/null

echo "make_icons: wrote $OUT_ICNS and $OUT_MENU"

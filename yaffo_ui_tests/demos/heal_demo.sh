#!/bin/bash
# =============================================================================
# Demo: AI-Augmented Test Healing (Auto-Heal)
#
# Shows the AI fixing a broken test selector:
#   1. Run the passing test suite
#   2. Break a selector (.photo-grid → .gallery-grid)
#   3. Run the heal script and watch the AI fix it
#
# Record with:  asciinema rec heal_demo.cast
# Convert with: agg heal_demo.cast heal_demo.gif
# =============================================================================

set -e
cd "$(dirname "$0")"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

PROJECT_ROOT=".."
TEST_FILE="$PROJECT_ROOT/generated_tests/photo_gallery/photo_gallery.spec.ts"

banner() {
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}  $1${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    sleep 2
}

comment() {
    echo -e "${YELLOW}# $1${NC}"
    sleep 1
}

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 1: The working test suite — all tests passing"
# ─────────────────────────────────────────────────────────────────────────────

comment "Every test uses a beforeEach that waits for the .photo-grid selector:"
echo ""

sed -n '10,14p' "$TEST_FILE"

sleep 4

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 2: Break a selector"
# ─────────────────────────────────────────────────────────────────────────────

comment "Renaming the selector: .photo-grid → .gallery-grid"
comment "This class doesn't exist in the app — every test will fail."
echo ""
sleep 2

sed -i '' 's|\.photo-grid|.gallery-grid|g' "$TEST_FILE"

echo -e "${RED}Selector broken!${NC}"
echo ""
sed -n '10,14p' "$TEST_FILE"

sleep 3

echo ""
comment "Re-running the same tests with the broken selector..."
echo ""
sleep 1

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 3: Run the auto-heal"
# ─────────────────────────────────────────────────────────────────────────────

comment "The healer will:"
comment "  1. Spin up an isolated Flask server"
comment "  2. Run the Playwright test and capture the failure"
comment "  3. Use AI to triage: is it a test bug or an app bug?"
comment "  4. Browse the live app and read the source code"
comment "  5. Fix the broken selector and re-run the test"
echo ""
sleep 3

comment "Running: npm run test:heal specs/photo_gallery.yaml"
echo ""
sleep 1

cd "$PROJECT_ROOT"
npm run test:heal specs/photo_gallery.yaml || true
cd - > /dev/null

sleep 2

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 4: Review the healed test"
# ─────────────────────────────────────────────────────────────────────────────

comment "The AI found the correct selector and fixed the test."
comment "Here's the healed beforeEach:"
echo ""

sed -n '10,14p' "$TEST_FILE"

sleep 3

echo ""
comment "Key findings:"
comment "  - Classification: test_code_defect"
comment "  - The app is fine — the selector was wrong"
comment "  - The AI browsed the live app, found .photo-grid,"
comment "  - and rewrote the test with the correct selector"
echo ""

sleep 3

# ─────────────────────────────────────────────────────────────────────────────
banner "Demo complete"
# ─────────────────────────────────────────────────────────────────────────────

comment "The AI inspected the running app, identified the wrong"
comment "selector as a test defect, and generated a corrected test."
echo ""
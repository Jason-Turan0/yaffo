#!/bin/bash
# =============================================================================
# Demo: AI-Augmented Test Diagnosis (Auto-Heal)
#
# Shows the AI diagnosing a real application regression:
#   1. Introduce a bug in the Flask route (page-size → PAGE_SIZE)
#   2. Run the heal script against the pagination test
#   3. Watch the AI triage the failure as an application_regression
#
# Record with:  asciinema rec demo_heal.cast
# Convert with: agg demo_heal.cast demo_heal.gif
# =============================================================================

set -e
cd "$(dirname "$0")"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

HOME_PY="../yaffo/routes/home.py"

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
banner "Step 1: The working test — pagination is passing"
# ─────────────────────────────────────────────────────────────────────────────

comment "Our photo gallery spec includes a pagination test."
comment "It sets page-size=10 and verifies navigation works."
echo ""
sleep 1

grep -n 'gallery_page_navigation_works' specs/photo_gallery.yaml -A 14

sleep 4

echo ""
comment "The generated Playwright test drives a real browser:"
echo ""

sed -n '77,112p' generated_tests/photo_gallery/photo_gallery.spec.ts

sleep 4

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 2: Introduce a bug in the application"
# ─────────────────────────────────────────────────────────────────────────────

comment "The Flask route reads the page-size query parameter."
comment "Let's see the original, working code:"
echo ""

grep -n 'page_size =' "$HOME_PY"

sleep 3

echo ""
comment "Now we introduce a bug: rename 'page-size' → 'PAGE_SIZE'."
comment "The frontend still sends ?page-size=10, but the backend"
comment "will ignore it and default to showing 25 photos."
echo ""
sleep 5

sed -i '' 's|request\.args\.get("page-size"|request.args.get("PAGE_SIZE"|' "$HOME_PY"

echo -e "${RED}Bug injected!${NC}"
echo ""
grep -n 'PAGE_SIZE\|page.size' "$HOME_PY"

sleep 5

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 3: Run the auto-heal diagnostic"
# ─────────────────────────────────────────────────────────────────────────────

comment "The healer will:"
comment "  1. Spin up an isolated Flask server with the buggy code"
comment "  2. Run the Playwright test and capture the failure"
comment "  3. Use AI to triage: is it a test bug or an app bug?"
comment "  4. Read the app source code and browse the live app"
comment "  5. Classify the root cause"
echo ""
sleep 3

comment "Running: npm run test:heal specs/photo_gallery.yaml"
echo ""
sleep 5

npm run test:heal specs/photo_gallery.yaml || true

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 4: Review the diagnosis"
# ─────────────────────────────────────────────────────────────────────────────

comment "The AI classified this as an application_regression."
comment "Let's see the full triage analysis:"
echo ""

cat generated_tests/photo_gallery/photo_gallery.triage_analysis.json | python3 -m json.tool

sleep 4

echo ""
comment "Key findings:"
comment "  - Classification: application_regression"
comment "  - The test code is correct (page-size=10 is right)"
comment "  - The backend changed 'page-size' to 'PAGE_SIZE'"
comment "  - No test fix was attempted — the app has the bug"
echo ""

sleep 3

# ─────────────────────────────────────────────────────────────────────────────
banner "Demo complete"
# ─────────────────────────────────────────────────────────────────────────────

comment "The AI didn't blindly try to 'fix' the test."
comment "It investigated the app source code, found the parameter"
comment "mismatch, and correctly identified it as a real regression."
echo ""
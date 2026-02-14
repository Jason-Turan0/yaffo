#!/bin/bash
# =============================================================================
# Demo: AI-Augmented UI Test Generation
#
# Record with:  asciinema rec demo_generation.cast
# Convert with: agg demo_generation.cast demo_generation.gif
# =============================================================================

set -e
cd "$(dirname "$0")"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

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
banner "Step 1: The Test Spec — plain English, no selectors"
# ─────────────────────────────────────────────────────────────────────────────

comment "This YAML file is all a human writes."
comment "It describes WHAT to test — the AI figures out HOW."
echo ""
sleep 1

cat specs/photo_gallery.yaml

sleep 4

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 2: Delete the existing generated tests"
# ─────────────────────────────────────────────────────────────────────────────

comment "Let's start from scratch. The generated tests folder has:"
echo ""
ls -la generated_tests/photo_gallery/
sleep 3

echo ""
comment "Deleting the generated tests (safe — it's in source control)."
echo ""
rm -rf generated_tests/photo_gallery
echo -e "${GREEN}Deleted generated_tests/photo_gallery/${NC}"
sleep 2

echo ""
comment "Confirmed — it's gone:"
echo ""
ls generated_tests/
sleep 2

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 3: Generate Playwright tests from the spec"
# ─────────────────────────────────────────────────────────────────────────────

comment "The AI will:"
comment "  1. Read the spec"
comment "  2. Read the app's source code (read-only, scoped access)"
comment "  3. Browse the live app with a real browser"
comment "  4. Generate TypeScript Playwright tests"
comment "  5. Type-check and run them"
echo ""
sleep 3

comment "Running: npm run generate:test specs/photo_gallery.yaml"
echo ""
sleep 1

npm run generate:test specs/photo_gallery.yaml

sleep 2

# ─────────────────────────────────────────────────────────────────────────────
banner "Step 4: Review what was generated"
# ─────────────────────────────────────────────────────────────────────────────

comment "Let's see what the AI produced:"
echo ""
ls -la generated_tests/photo_gallery/
sleep 3

echo ""
comment "The generated Playwright test:"
echo ""
cat generated_tests/photo_gallery/photo_gallery.spec.ts

sleep 4

# ─────────────────────────────────────────────────────────────────────────────
banner "Demo complete"
# ─────────────────────────────────────────────────────────────────────────────

comment "From a 62-line YAML spec to working Playwright tests."
comment "The AI read the source code, browsed the live app,"
comment "and generated tests with real selectors and assertions."
echo ""
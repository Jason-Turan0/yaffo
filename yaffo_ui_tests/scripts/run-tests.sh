#!/bin/bash
# Run UI tests in isolated test environment

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UI_TESTS_DIR="$(dirname "$SCRIPT_DIR")"
YAFFO_DIR="$(dirname "$UI_TESTS_DIR")"

# Activate virtual environment
source "$YAFFO_DIR/venv/bin/activate"

# Default port
PORT="${PORT:-5001}"

# Cleanup temp directory after tests (default: true)
CLEANUP="${CLEANUP:-true}"

echo "=== Setting up test environment ==="

# Create temp directory with timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
TEMP_DIR=$(mktemp -d -t "yaffo_test_${TIMESTAMP}_XXX")
echo "Temp directory: $TEMP_DIR"

# Create directory structure
mkdir -p "$TEMP_DIR/organized"
mkdir -p "$TEMP_DIR/thumbnails"
mkdir -p "$TEMP_DIR/temp"
mkdir -p "$TEMP_DIR/duplicates"

# Copy test photos
if [ -d "$UI_TESTS_DIR/test_data/photos" ]; then
    cp "$UI_TESTS_DIR/test_data/photos"/* "$TEMP_DIR/organized/" 2>/dev/null || true
    echo "Copied test photos"
fi

# Copy or create test database
if [ -f "$UI_TESTS_DIR/test_data/database/yaffo.db" ]; then
    cp "$UI_TESTS_DIR/test_data/database/yaffo.db" "$TEMP_DIR/yaffo.db"
    echo "Copied test database"
else
    touch "$TEMP_DIR/yaffo.db"
    echo "Created empty database"
fi

touch "$TEMP_DIR/yaffo-huey.db"

# Export environment
export YAFFO_DATA_DIR="$TEMP_DIR"
export FLASK_APP="yaffo.app:create_app"
export FLASK_ENV="testing"

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "=== Cleaning up ==="

    if [ -n "$FLASK_PID" ]; then
        echo "Stopping Flask (PID: $FLASK_PID)"
        kill $FLASK_PID 2>/dev/null || true
        wait $FLASK_PID 2>/dev/null || true
    fi

    if [ -n "$HUEY_PID" ]; then
        echo "Stopping Huey (PID: $HUEY_PID)"
        kill $HUEY_PID 2>/dev/null || true
        wait $HUEY_PID 2>/dev/null || true
    fi

    if [ -d "$TEMP_DIR" ] && [ "$CLEANUP" = "true" ]; then
        echo "Removing temp directory: $TEMP_DIR"
        rm -rf "$TEMP_DIR"
    else
        echo "Keeping temp directory: $TEMP_DIR"
    fi
}

trap cleanup EXIT

# Index test photos
echo ""
echo "=== Indexing test photos ==="
python "$SCRIPT_DIR/seed_database.py"

# Start Flask
echo ""
echo "=== Starting Flask on port $PORT ==="
cd "$YAFFO_DIR"
python -m flask run --host=127.0.0.1 --port=$PORT --no-reload > "$TEMP_DIR/flask.log" 2>&1 &
FLASK_PID=$!
echo "Flask PID: $FLASK_PID"

# Wait for Flask to be ready
echo "Waiting for Flask to start..."
for i in {1..30}; do
    if curl -s "http://127.0.0.1:$PORT/" > /dev/null 2>&1; then
        echo "Flask is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "Flask failed to start. Log:"
        cat "$TEMP_DIR/flask.log"
        exit 1
    fi
    sleep 1
done

# Run tests
echo ""
echo "=== Running Playwright tests ==="
cd "$UI_TESTS_DIR"
export BASE_URL="http://127.0.0.1:$PORT"

npx playwright test --project=chromium "$@"
TEST_EXIT_CODE=$?

echo ""
echo "=== Tests completed with exit code: $TEST_EXIT_CODE ==="
exit $TEST_EXIT_CODE
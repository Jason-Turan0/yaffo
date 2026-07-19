#!/bin/sh
# Restore demo-a and demo-b to the golden state saved by save-golden.sh.
# Idempotent and safe to re-run if interrupted partway through (e.g. the
# process is killed between stopping the apps and finishing the file swap):
# each device's swap goes through a staging directory and a self-heal check
# at the top completes (never reverts) any swap a previous run didn't finish,
# so re-running always converges on the golden state.
#
# Identity keys (runtime/{a,b}-identity) are a separate volume and are never
# touched: the golden database snapshots refer to those specific device
# identities.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
compose="docker compose --env-file $script_dir/.env -f $script_dir/compose.local.yml"
runtime_dir="$script_dir/runtime"
golden_dir="$script_dir/golden"

restore_one() {
    device="$1"
    golden_src="$golden_dir/$device"
    live="$runtime_dir/$device"
    stage="$runtime_dir/$device.staging"

    if [ ! -d "$golden_src" ]; then
        echo "No golden/$device to restore from; run save-golden.sh first" >&2
        exit 1
    fi

    # Self-heal: a previous run staged a fresh copy but didn't finish moving
    # it into place. Finish forward rather than discarding the staged copy.
    if [ -d "$stage" ] && [ ! -e "$live" ]; then
        mv "$stage" "$live"
    fi
    rm -rf "$stage"

    cp -R "$golden_src" "$stage"
    rm -rf "$live"
    mv "$stage" "$live"
    echo "Restored runtime/$device from golden"
}

echo "Stopping demo-a and demo-b..."
$compose stop demo-a demo-b

restore_one a
restore_one b

echo "Starting demo-a and demo-b..."
$compose up -d demo-a demo-b

echo "Waiting for both to report healthy..."
attempt=0
while true
do
    a_status=$($compose ps --format json demo-a 2>/dev/null | python3 -c "import json,sys; lines=[l for l in sys.stdin if l.strip()]; print(json.loads(lines[0])['Health'] if lines else 'missing')" 2>/dev/null || echo "unknown")
    b_status=$($compose ps --format json demo-b 2>/dev/null | python3 -c "import json,sys; lines=[l for l in sys.stdin if l.strip()]; print(json.loads(lines[0])['Health'] if lines else 'missing')" 2>/dev/null || echo "unknown")
    if [ "$a_status" = "healthy" ] && [ "$b_status" = "healthy" ]; then
        break
    fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Timed out waiting for demo-a/demo-b to report healthy (a=$a_status b=$b_status)" >&2
        exit 1
    fi
    sleep 2
done

echo "Smoke-testing both devices..."
a_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8080/" -H "Host: ${DEMO_A_HOST:-demo-a.localhost}" || echo "000")
b_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8080/" -H "Host: ${DEMO_B_HOST:-demo-b.localhost}" || echo "000")
if [ "$a_code" != "200" ] || [ "$b_code" != "200" ]; then
    echo "Smoke test failed (demo-a=$a_code demo-b=$b_code)" >&2
    exit 1
fi

echo "Reset complete. demo-a=$a_code demo-b=$b_code"

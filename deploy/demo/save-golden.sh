#!/bin/sh
# Freeze the seeded runtime/{a,b} data dirs as the immutable golden state
# reset-local.sh restores from. Run this once after deploy/demo/seed-local.sh
# has finished and you're happy with the result.
#
# Stops both apps first — SQLite must be closed, not copied live (a WAL file
# mid-checkpoint is not a valid snapshot). Identity keys are a separate volume
# (runtime/{a,b}-identity) and are never touched here: the golden database
# snapshots refer to those specific device identities and must keep matching
# them across every future reset.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
compose="docker compose --env-file $script_dir/.env -f $script_dir/compose.local.yml"
runtime_dir="$script_dir/runtime"
golden_dir="$script_dir/golden"

echo "Stopping demo-a and demo-b so SQLite closes cleanly..."
$compose stop demo-a demo-b

save_one() {
    device="$1"
    src="$runtime_dir/$device"
    stage="$golden_dir/$device.staging"
    dest="$golden_dir/$device"

    if [ ! -d "$src" ]; then
        echo "No runtime/$device to snapshot; run seed-local.sh first" >&2
        exit 1
    fi

    rm -rf "$stage"
    mkdir -p "$golden_dir"
    cp -R "$src" "$stage"
    # yaffo.db-wal/-shm are NOT disposable: SQLite in WAL mode leaves recent
    # writes there until an auto-checkpoint folds them into yaffo.db, which a
    # light seeding run may never trigger. Deleting them here silently
    # dropped all seeded data once (2026-07-19) while leaving orphaned
    # thumbnail files behind. Only *.log is a true operational artifact.
    rm -f "$stage"/*.log

    rm -rf "$dest"
    mv "$stage" "$dest"
    echo "Saved golden/$device"
}

save_one a
save_one b

echo "Restarting demo-a and demo-b..."
$compose start demo-a demo-b

echo "Golden state saved. Restore it anytime with deploy/demo/reset-local.sh"

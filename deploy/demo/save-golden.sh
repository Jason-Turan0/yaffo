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
    # Operational artifacts from this run, not part of the golden state: every
    # reset should start with clean logs, and a checkpointed DB doesn't need
    # its WAL/SHM sidecars (SQLite regenerates them as needed).
    rm -f "$stage"/*.log "$stage"/yaffo.db-wal "$stage"/yaffo.db-shm

    rm -rf "$dest"
    mv "$stage" "$dest"
    echo "Saved golden/$device"
}

save_one a
save_one b

echo "Restarting demo-a and demo-b..."
$compose start demo-a demo-b

echo "Golden state saved. Restore it anytime with deploy/demo/reset-local.sh"

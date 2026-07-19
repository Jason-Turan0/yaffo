#!/usr/bin/env bash
# Freeze the seeded production data as the golden state the daily reset
# restores from. Mirrors deploy/demo/save-golden.sh, run remotely over SSH.
#
# Run after deploy/demo/seed-prod.sh, once you're happy with the result.
#
# Usage: ./save-golden-prod.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(terraform -chdir="$script_dir" output -raw project_id)"
vm="$(terraform -chdir="$script_dir" output -raw vm_name)"
zone="$(terraform -chdir="$script_dir" output -raw vm_zone)"

KEY="$script_dir/yaffo-demo-admin-key"
if [[ ! -f "$KEY" ]]; then
    echo "No automation SSH key at $KEY — see deploy.sh for how to generate one." >&2
    exit 1
fi
SSH_ARGS=(--project="$project" --zone="$zone" --tunnel-through-iap --ssh-key-file="$KEY")

echo "Saving golden state on $vm..."
gcloud compute ssh "$vm" "${SSH_ARGS[@]}" --quiet --command='
    set -eu
    DATA_ROOT=/var/lib/yaffo-demo
    COMPOSE="$DATA_ROOT/bin/docker-compose --env-file $DATA_ROOT/deploy/.env -f $DATA_ROOT/deploy/compose.prod.yml"

    echo "Stopping demo-a and demo-b so SQLite closes cleanly..."
    sudo sh -c "$COMPOSE stop demo-a demo-b"

    for device in a b; do
        src="$DATA_ROOT/$device"
        stage="$DATA_ROOT/golden/$device.staging"
        dest="$DATA_ROOT/golden/$device"

        sudo rm -rf "$stage"
        sudo mkdir -p "$DATA_ROOT/golden"
        sudo cp -R "$src" "$stage"
        # yaffo.db-wal/-shm are NOT disposable: SQLite in WAL mode leaves
        # recent writes there until an auto-checkpoint folds them into
        # yaffo.db, which a light seeding run may never trigger. Deleting
        # them here silently dropped all seeded data once (2026-07-19) while
        # leaving orphaned thumbnail files behind. Only *.log is a true
        # operational artifact.
        sudo rm -f "$stage"/*.log

        sudo rm -rf "$dest"
        sudo mv "$stage" "$dest"
        echo "Saved golden/$device"
    done

    echo "Restarting demo-a and demo-b..."
    sudo sh -c "$COMPOSE start demo-a demo-b"
'

echo
echo "Golden state saved. Restore it on demand with deploy/demo/reset-prod.sh,"
echo "or wait for the next scheduled VM start (the startup script restores it automatically)."

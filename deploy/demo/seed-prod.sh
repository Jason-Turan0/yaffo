#!/usr/bin/env bash
# Seed the production demo devices: upload fixture media (once) and the seed
# scripts, then run them on the VM against compose.prod.yml. Mirrors
# deploy/demo/seed-local.sh's steps, but the actual `docker compose run`
# orchestration happens on the VM itself (files/remote-seed.sh) since that's
# where the compose stack and the deployed .env live.
#
# Run after deploy.sh has deployed the containers at least once. Idempotent:
# fixture media is only uploaded if not already present; re-running re-seeds
# (seed_demo.py itself skips content that's already indexed).
#
# Usage: ./seed-prod.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
project="$(terraform -chdir="$script_dir" output -raw project_id)"
vm="$(terraform -chdir="$script_dir" output -raw vm_name)"
zone="$(terraform -chdir="$script_dir" output -raw vm_zone)"

KEY="$script_dir/yaffo-demo-admin-key"
if [[ ! -f "$KEY" ]]; then
    echo "No automation SSH key at $KEY — see deploy.sh for how to generate one." >&2
    exit 1
fi
SSH_ARGS=(--project="$project" --zone="$zone" --tunnel-through-iap --ssh-key-file="$KEY")

# One trap for every temp file/dir this script creates below — a second
# `trap ... EXIT` would silently replace the first and skip its cleanup.
fixture_bundle=""
seed_stage=""
seed_bundle=""
cleanup() {
    rm -f "$fixture_bundle" "$seed_bundle"
    rm -rf "$seed_stage"
}
trap cleanup EXIT

echo "Checking whether fixture media is already on the VM..."
if gcloud compute ssh "$vm" "${SSH_ARGS[@]}" --quiet \
    --command="test -n \"\$(ls -A /var/lib/yaffo-demo/fixtures/a 2>/dev/null)\"" >/dev/null 2>&1; then
    echo "Fixture media already present — skipping upload."
else
    echo "Uploading fixture media (Bennett is synthetic; Obama is real, public-domain"
    echo "National Archives photography — see yaffo_ui_tests/test_data/obama/ATTRIBUTION.md)..."
    fixture_bundle="$(mktemp -t yaffo-demo-fixtures.XXXXXX.tar.gz)"
    # COPYFILE_DISABLE stops macOS tar from synthesizing AppleDouble ._files
    # from xattrs (e.g. quarantine/provenance) — those aren't real images and
    # break the indexer on extraction.
    COPYFILE_DISABLE=1 tar -czf "$fixture_bundle" -C "$repo_root/yaffo_ui_tests/test_data" bennett mp4 obama/images

    gcloud compute scp "$fixture_bundle" "$vm:/tmp/yaffo-demo-fixtures.tar.gz" "${SSH_ARGS[@]}" --quiet

    gcloud compute ssh "$vm" "${SSH_ARGS[@]}" --quiet --command="
        set -eu
        sudo rm -rf /tmp/yaffo-demo-fixtures-extract
        sudo mkdir -p /tmp/yaffo-demo-fixtures-extract
        sudo tar -xzf /tmp/yaffo-demo-fixtures.tar.gz -C /tmp/yaffo-demo-fixtures-extract
        sudo cp -R /tmp/yaffo-demo-fixtures-extract/bennett/. /var/lib/yaffo-demo/fixtures/a/
        sudo cp /tmp/yaffo-demo-fixtures-extract/mp4/*.mp4 /var/lib/yaffo-demo/fixtures/a/
        sudo cp -R /tmp/yaffo-demo-fixtures-extract/obama/images/. /var/lib/yaffo-demo/fixtures/b/
        sudo chown -R 10001:10001 /var/lib/yaffo-demo/fixtures
        sudo rm -rf /tmp/yaffo-demo-fixtures-extract /tmp/yaffo-demo-fixtures.tar.gz
    "
fi

echo "Uploading seed scripts..."
seed_stage="$(mktemp -d -t yaffo-demo-seed-stage.XXXXXX)"
seed_bundle="$(mktemp -t yaffo-demo-seed.XXXXXX.tar.gz)"

cp "$script_dir/seed_demo.py" "$seed_stage/"
cp "$script_dir/files/remote-seed.sh" "$seed_stage/"
cp "$repo_root/yaffo_ui_tests/scripts/seed_database.py" "$seed_stage/"
cp "$repo_root/yaffo_ui_tests/test_data/bennett_face_assignments.json" "$seed_stage/"
mkdir -p "$seed_stage/scripts"
cp "$repo_root/scripts/__init__.py" "$seed_stage/scripts/"
cp "$repo_root/scripts/seed_automations.py" "$seed_stage/scripts/"
COPYFILE_DISABLE=1 tar -czf "$seed_bundle" -C "$seed_stage" .

gcloud compute scp "$seed_bundle" "$vm:/tmp/yaffo-demo-seed.tar.gz" "${SSH_ARGS[@]}" --quiet

gcloud compute ssh "$vm" "${SSH_ARGS[@]}" --quiet --command="
    set -eu
    sudo rm -rf /var/lib/yaffo-demo/deploy/seed
    sudo mkdir -p /var/lib/yaffo-demo/deploy/seed
    sudo tar -xzf /tmp/yaffo-demo-seed.tar.gz -C /var/lib/yaffo-demo/deploy/seed
    sudo rm -f /tmp/yaffo-demo-seed.tar.gz
    sudo chmod +x /var/lib/yaffo-demo/deploy/seed/remote-seed.sh
    sudo /var/lib/yaffo-demo/deploy/seed/remote-seed.sh
"

echo
echo "Seeded. Next: deploy/demo/save-golden-prod.sh"

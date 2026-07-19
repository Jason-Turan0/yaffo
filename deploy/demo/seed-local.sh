#!/bin/sh
# Seed the local demo stack's golden state: copy the synthetic fixture media in,
# index+seed both devices inside the built image, then cross-pair them.
#
# Fixture note: device A (Bennett) is a synthetic library of generated people and
# scenes, the same one the two-instance sharing UI-test sandbox uses. Device B
# (Obama) is real photography from the Barack Obama Presidential Library/NARA,
# public domain — see yaffo_ui_tests/test_data/obama/ATTRIBUTION.md for the
# attribution and source record. deploy/demo/fixtures/ is gitignored, so nothing
# here is committed.
#
# Prerequisites: deploy/demo/init-local.sh has run, and the yaffo-demo:local
# image is built (`docker compose ... build`).
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
compose="docker compose --env-file $script_dir/.env -f $script_dir/compose.local.yml"

fixtures_a="$script_dir/fixtures/a"
fixtures_b="$script_dir/fixtures/b"

if [ -z "$(ls -A "$fixtures_a" 2>/dev/null)" ]; then
    echo "Copying synthetic fixture media for device A (Bennett)..."
    cp -R "$repo_root/yaffo_ui_tests/test_data/bennett/." "$fixtures_a/"
    cp "$repo_root/yaffo_ui_tests/test_data/mp4/"*.mp4 "$fixtures_a/"
fi
if [ -z "$(ls -A "$fixtures_b" 2>/dev/null)" ]; then
    echo "Copying real, public-domain fixture media for device B (Obama)..."
    cp -R "$repo_root/yaffo_ui_tests/test_data/obama/images/." "$fixtures_b/"
fi

seed_mounts="-v $script_dir/seed_demo.py:/mnt/seed_demo.py:ro \
    -v $repo_root/yaffo_ui_tests/scripts/seed_database.py:/mnt/seed_database.py:ro \
    -v $repo_root/yaffo_ui_tests/test_data/bennett_face_assignments.json:/mnt/bennett_face_assignments.json:ro \
    -v $repo_root/scripts/__init__.py:/mnt/scripts/__init__.py:ro \
    -v $repo_root/scripts/seed_automations.py:/mnt/scripts/seed_automations.py:ro"

run_seed() {
    service="$1"
    role="$2"
    echo "Seeding $service (role=$role)..."
    # shellcheck disable=SC2086
    $compose run --rm -T -e YAFFO_DEMO_MODE=0 $seed_mounts "$service" \
        python /mnt/seed_demo.py seed --role "$role"
}

run_label() {
    service="$1"
    echo "Classifying labels on $service..."
    # Separate process from `seed`: InsightFace (indexing) + CLIP
    # (classification) loaded together in one process exceeds the container's
    # mem_limit and gets OOM-killed.
    # shellcheck disable=SC2086
    $compose run --rm -T -e YAFFO_DEMO_MODE=0 $seed_mounts "$service" \
        python /mnt/seed_demo.py label
}

run_seed demo-a source
run_seed demo-b receiver
run_label demo-a
run_label demo-b

identity_json() {
    python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    info = json.load(f)
print(info['device_id'])
print(info['pubkey'])
print(info['display_name'])
" "$1"
}

a_info=$(identity_json "$script_dir/runtime/a-identity/device-id.json")
a_device_id=$(echo "$a_info" | sed -n '1p')
a_pubkey=$(echo "$a_info" | sed -n '2p')
a_display_name=$(echo "$a_info" | sed -n '3p')

b_info=$(identity_json "$script_dir/runtime/b-identity/device-id.json")
b_device_id=$(echo "$b_info" | sed -n '1p')
b_pubkey=$(echo "$b_info" | sed -n '2p')
b_display_name=$(echo "$b_info" | sed -n '3p')

echo "Pairing demo-a -> demo-b ($b_display_name)..."
# shellcheck disable=SC2086
$compose run --rm -T -e YAFFO_DEMO_MODE=0 $seed_mounts demo-a \
    python /mnt/seed_demo.py pair --role source \
    --peer-device-id "$b_device_id" --peer-pubkey "$b_pubkey" --peer-display-name "$b_display_name"

echo "Pairing demo-b -> demo-a ($a_display_name)..."
# shellcheck disable=SC2086
$compose run --rm -T -e YAFFO_DEMO_MODE=0 $seed_mounts demo-b \
    python /mnt/seed_demo.py pair --role receiver \
    --peer-device-id "$a_device_id" --peer-pubkey "$a_pubkey" --peer-display-name "$a_display_name"

echo "Golden state seeded. Restart the running stack to pick it up:"
echo "  $compose restart demo-a demo-b"

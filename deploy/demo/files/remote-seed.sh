#!/bin/sh
# Runs ON the demo VM — uploaded and invoked by deploy/demo/seed-prod.sh, not
# meant to be run directly. Mirrors deploy/demo/seed-local.sh's logic against
# compose.prod.yml and the production /var/lib/yaffo-demo paths, using the
# seed scripts and fixture media seed-prod.sh has already placed here.
set -eu

DATA_ROOT=/var/lib/yaffo-demo
SEED_DIR="$DATA_ROOT/deploy/seed"
COMPOSE_BIN="$DATA_ROOT/bin/docker-compose"

compose() {
    "$COMPOSE_BIN" --env-file "$DATA_ROOT/deploy/.env" -f "$DATA_ROOT/deploy/compose.prod.yml" "$@"
}

seed_mounts="-v $SEED_DIR/seed_demo.py:/mnt/seed_demo.py:ro \
    -v $SEED_DIR/seed_database.py:/mnt/seed_database.py:ro \
    -v $SEED_DIR/bennett_face_assignments.json:/mnt/bennett_face_assignments.json:ro \
    -v $SEED_DIR/scripts/__init__.py:/mnt/scripts/__init__.py:ro \
    -v $SEED_DIR/scripts/seed_automations.py:/mnt/scripts/seed_automations.py:ro"

run_seed() {
    service="$1"
    role="$2"
    echo "Seeding $service (role=$role)..."
    # shellcheck disable=SC2086
    compose run --rm -T -e YAFFO_DEMO_MODE=0 $seed_mounts "$service" \
        python /mnt/seed_demo.py seed --role "$role"
}

run_label() {
    service="$1"
    echo "Classifying labels on $service..."
    # Separate process from `seed`: InsightFace (indexing) + CLIP
    # (classification) loaded together in one process exceeds the container's
    # mem_limit and gets OOM-killed.
    # shellcheck disable=SC2086
    compose run --rm -T -e YAFFO_DEMO_MODE=0 $seed_mounts "$service" \
        python /mnt/seed_demo.py label
}

run_seed demo-a source
run_seed demo-b receiver
run_label demo-a
run_label demo-b

identity_field() {
    python3 -c "import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$1" "$2"
}

a_json="$DATA_ROOT/identities/a/device-id.json"
b_json="$DATA_ROOT/identities/b/device-id.json"
a_device_id=$(identity_field "$a_json" device_id)
a_pubkey=$(identity_field "$a_json" pubkey)
a_name=$(identity_field "$a_json" display_name)
b_device_id=$(identity_field "$b_json" device_id)
b_pubkey=$(identity_field "$b_json" pubkey)
b_name=$(identity_field "$b_json" display_name)

echo "Pairing demo-a -> demo-b ($b_name)..."
# shellcheck disable=SC2086
compose run --rm -T -e YAFFO_DEMO_MODE=0 $seed_mounts demo-a \
    python /mnt/seed_demo.py pair --role source \
    --peer-device-id "$b_device_id" --peer-pubkey "$b_pubkey" --peer-display-name "$b_name"

echo "Pairing demo-b -> demo-a ($a_name)..."
# shellcheck disable=SC2086
compose run --rm -T -e YAFFO_DEMO_MODE=0 $seed_mounts demo-b \
    python /mnt/seed_demo.py pair --role receiver \
    --peer-device-id "$a_device_id" --peer-pubkey "$a_pubkey" --peer-display-name "$a_name"

echo
echo "Seeding complete. Next: deploy/demo/save-golden-prod.sh"

#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
umask 077

mkdir -p \
    "$script_dir/fixtures/a" \
    "$script_dir/fixtures/b" \
    "$script_dir/runtime/a" \
    "$script_dir/runtime/a-identity" \
    "$script_dir/runtime/b" \
    "$script_dir/runtime/b-identity" \
    "$script_dir/secrets"

for name in demo-a-flask-secret demo-b-flask-secret; do
    target="$script_dir/secrets/$name"
    if [ ! -f "$target" ]; then
        openssl rand -hex 32 > "$target"
    fi
    chmod 0600 "$target"
done

if [ ! -f "$script_dir/.env" ]; then
    cp "$script_dir/.env.example" "$script_dir/.env"
fi

echo "Local demo directories and secrets are ready."


#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "Usage: $0 <unique-image-tag>" >&2
    exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
terraform_dir="$repo_root/deploy/gcp"
image_repo="$(terraform -chdir="$terraform_dir" output -raw image_repo)"
registry_host="${image_repo%%/*}"
image="${image_repo}/yaffo-demo:$1"
metadata_file="$(mktemp -t yaffo-demo-build.XXXXXX.json)"
trap 'rm -f "$metadata_file"' EXIT

gcloud auth configure-docker "$registry_host" --quiet
docker buildx build \
    --file "$repo_root/Dockerfile" \
    --platform linux/amd64 \
    --tag "$image" \
    --metadata-file "$metadata_file" \
    --push \
    "$repo_root"

digest="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["containerimage.digest"])' "$metadata_file")"
echo
echo "Immutable application image:"
echo "${image_repo}/yaffo-demo@${digest}"


#!/usr/bin/env bash
set -euo pipefail

digest_reference='^.+@sha256:[0-9a-f]{64}$'
if [[ $# -ne 2 || ! "$1" =~ $digest_reference || ! "$2" =~ $digest_reference ]]; then
    echo "Usage: $0 <yaffo-image@sha256:digest> <caddy-image@sha256:digest>" >&2
    exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
terraform_dir="$repo_root/deploy/gcp"
project="$(terraform -chdir="$terraform_dir" output -raw project_id)"
vm="$(terraform -chdir="$terraform_dir" output -raw vm_name)"
zone="$(terraform -chdir="$terraform_dir" output -raw vm_zone)"
walkthrough_host="$(terraform -chdir="$terraform_dir" output -raw walkthrough_domain)"
device_a_host="$(terraform -chdir="$terraform_dir" output -raw demo_a_domain)"
device_b_host="$(terraform -chdir="$terraform_dir" output -raw demo_b_domain)"
hub_url="$(terraform -chdir="$terraform_dir" output -raw hub_url)"
image_repo="$(terraform -chdir="$terraform_dir" output -raw image_repo)"
registry_host="${image_repo%%/*}"

bundle_dir="$(mktemp -d -t yaffo-demo-deploy.XXXXXX)"
bundle_archive="$(mktemp -t yaffo-demo-deploy.XXXXXX.tar.gz)"
trap 'rm -rf "$bundle_dir"; rm -f "$bundle_archive"' EXIT

cp "$script_dir/compose.prod.yml" "$bundle_dir/"
cp "$script_dir/Caddyfile" "$bundle_dir/"
cp -R "$script_dir/walkthrough" "$bundle_dir/"
printf '%s\n' \
    "YAFFO_IMAGE=$1" \
    "CADDY_IMAGE=$2" \
    "DEMO_WALKTHROUGH_HOST=$walkthrough_host" \
    "DEMO_A_HOST=$device_a_host" \
    "DEMO_B_HOST=$device_b_host" \
    "YAFFO_HUB_URL=$hub_url" > "$bundle_dir/.env"
tar -czf "$bundle_archive" -C "$bundle_dir" .

gcloud compute scp "$bundle_archive" "$vm:/tmp/yaffo-demo-deploy.tar.gz" \
    --project="$project" \
    --zone="$zone" \
    --tunnel-through-iap \
    --quiet

gcloud compute ssh "$vm" \
    --project="$project" \
    --zone="$zone" \
    --tunnel-through-iap \
    --quiet \
    --command="
        set -eu
        sudo mkdir -p /var/lib/yaffo-demo/deploy
        sudo find /var/lib/yaffo-demo/deploy -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
        sudo tar -xzf /tmp/yaffo-demo-deploy.tar.gz -C /var/lib/yaffo-demo/deploy
        sudo chmod 0600 /var/lib/yaffo-demo/deploy/.env
        sudo docker-credential-gcr configure-docker --registries='$registry_host'
        cd /var/lib/yaffo-demo/deploy
        sudo /var/lib/yaffo-demo/bin/docker-compose --env-file .env -f compose.prod.yml config --quiet
        sudo /var/lib/yaffo-demo/bin/docker-compose --env-file .env -f compose.prod.yml pull
        sudo /var/lib/yaffo-demo/bin/docker-compose --env-file .env -f compose.prod.yml up --detach --remove-orphans
        sudo /var/lib/yaffo-demo/bin/docker-compose --env-file .env -f compose.prod.yml ps
    "

echo
echo "Deployment submitted. Public endpoints may wait briefly for health checks and ACME:"
echo "  https://$walkthrough_host"
echo "  https://$device_a_host"
echo "  https://$device_b_host"


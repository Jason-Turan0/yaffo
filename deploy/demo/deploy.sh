#!/usr/bin/env bash
set -euo pipefail

digest_reference='^.+@sha256:[0-9a-f]{64}$'
if [[ $# -ne 2 || ! "$1" =~ $digest_reference || ! "$2" =~ $digest_reference ]]; then
    echo "Usage: $0 <yaffo-image@sha256:digest> <caddy-image@sha256:digest>" >&2
    exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(terraform -chdir="$script_dir" output -raw project_id)"
vm="$(terraform -chdir="$script_dir" output -raw vm_name)"
zone="$(terraform -chdir="$script_dir" output -raw vm_zone)"
walkthrough_host="$(terraform -chdir="$script_dir" output -raw walkthrough_domain)"
device_a_host="$(terraform -chdir="$script_dir" output -raw demo_a_domain)"
device_b_host="$(terraform -chdir="$script_dir" output -raw demo_b_domain)"
hub_url="$(terraform -chdir="$script_dir" output -raw hub_url)"
image_repo="$(terraform -chdir="$script_dir" output -raw image_repo)"
registry_host="${image_repo%%/*}"

# Dedicated passphrase-less automation key (generated once, gitignored; its
# pubkey ships to the VM via the admin_ssh_pubkey Terraform variable). Personal
# keys tend to carry passphrases, which fail silently without a TTY — same
# lesson as deploy/hub.
KEY="$script_dir/yaffo-demo-admin-key"
if [[ ! -f "$KEY" ]]; then
    echo "No automation SSH key at $KEY — generate one and re-apply Terraform:"
    echo "  ssh-keygen -t ed25519 -N \"\" -f $KEY -C yaffo-demo-automation"
    echo "  (then set admin_ssh_pubkey in terraform.tfvars to the .pub contents)"
    exit 1
fi
SSH_ARGS=(--project="$project" --zone="$zone" --tunnel-through-iap --ssh-key-file="$KEY")

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
    "${SSH_ARGS[@]}" \
    --quiet

gcloud compute ssh "$vm" \
    "${SSH_ARGS[@]}" \
    --quiet \
    --command="
        set -eu
        sudo mkdir -p /var/lib/yaffo-demo/deploy
        sudo find /var/lib/yaffo-demo/deploy -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
        sudo tar -xzf /tmp/yaffo-demo-deploy.tar.gz -C /var/lib/yaffo-demo/deploy
        sudo chmod 0600 /var/lib/yaffo-demo/deploy/.env
        DOCKER_CONFIG_DIR=/var/lib/yaffo-demo/deploy/.docker
        sudo mkdir -p \"\$DOCKER_CONFIG_DIR\"
        sudo env DOCKER_CONFIG=\"\$DOCKER_CONFIG_DIR\" docker-credential-gcr configure-docker --registries='$registry_host'
        ENV_FILE=/var/lib/yaffo-demo/deploy/.env
        BUNDLE=/var/lib/yaffo-demo/deploy/compose.prod.yml
        sudo env DOCKER_CONFIG=\"\$DOCKER_CONFIG_DIR\" /var/lib/yaffo-demo/bin/docker-compose --env-file \"\$ENV_FILE\" -f \"\$BUNDLE\" config --quiet
        sudo env DOCKER_CONFIG=\"\$DOCKER_CONFIG_DIR\" /var/lib/yaffo-demo/bin/docker-compose --env-file \"\$ENV_FILE\" -f \"\$BUNDLE\" pull
        sudo env DOCKER_CONFIG=\"\$DOCKER_CONFIG_DIR\" /var/lib/yaffo-demo/bin/docker-compose --env-file \"\$ENV_FILE\" -f \"\$BUNDLE\" up --detach --remove-orphans
        sudo /var/lib/yaffo-demo/bin/docker-compose --env-file \"\$ENV_FILE\" -f \"\$BUNDLE\" ps
    "

echo
echo "Deployment submitted. Public endpoints may wait briefly for health checks and ACME:"
echo "  https://$walkthrough_host"
echo "  https://$device_a_host"
echo "  https://$device_b_host"


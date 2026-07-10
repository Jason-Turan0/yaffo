#!/usr/bin/env bash
# Pushes the hub code (hub/ at the repo root) to the yaffo-hub VM and
# restarts the service. Terraform owns the infrastructure; this owns code
# delivery. Idempotent: run it for the first deploy and for every code
# change after. Run from deploy/hub/ after `terraform apply`.
#
# Usage: ./deploy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB_SRC="$(cd "$SCRIPT_DIR/../../hub" && pwd)"

# Read the deployment coordinates from Terraform outputs so the Terraform
# config stays the single source of truth for names/zones.
PROJECT=$(terraform -chdir="$SCRIPT_DIR" output -raw project_id)
VM=$(terraform -chdir="$SCRIPT_DIR" output -raw vm_name)
ZONE=$(terraform -chdir="$SCRIPT_DIR" output -raw vm_zone)
DOMAIN=$(terraform -chdir="$SCRIPT_DIR" output -raw hub_domain)
HUB_PORT=$(terraform -chdir="$SCRIPT_DIR" output -raw hub_port)

echo "Deploying $HUB_SRC to $VM ($ZONE, project $PROJECT), domain $DOMAIN"

# Dedicated passphrase-less automation key (generated once, gitignored; its
# pubkey ships to the VM via the admin_ssh_pubkey Terraform variable).
# Personal keys tend to carry passphrases, which fail silently without a TTY
# — same lesson as the POC's gcp scripts.
KEY="$SCRIPT_DIR/yaffo-hub-admin-key"
if [[ ! -f "$KEY" ]]; then
  echo "No automation SSH key at $KEY — generate one and re-apply Terraform:"
  echo "  ssh-keygen -t ed25519 -N \"\" -f $KEY -C yaffo-hub-automation"
  echo "  (then set admin_ssh_pubkey in terraform.tfvars to the .pub contents)"
  exit 1
fi
SSH=(gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap --ssh-key-file="$KEY")

echo "Packaging hub code..."
TARBALL=$(mktemp -t yaffo_hub_code.XXXXXX.tar.gz)
trap 'rm -f "$TARBALL"' EXIT
tar czf "$TARBALL" -C "$HUB_SRC" pyproject.toml yaffo_hub

echo "Copying to the VM..."
gcloud compute scp "$TARBALL" "$VM:/tmp/yaffo_hub_code.tar.gz" --zone="$ZONE" --project="$PROJECT" --tunnel-through-iap --ssh-key-file="$KEY" --quiet

echo "Installing and restarting yaffo-hub.service..."
"${SSH[@]}" --command="
  set -e
  sudo rm -rf /opt/yaffo-hub/src
  sudo mkdir -p /opt/yaffo-hub/src
  sudo tar xzf /tmp/yaffo_hub_code.tar.gz -C /opt/yaffo-hub/src
  if [ ! -d /opt/yaffo-hub/venv ]; then
    sudo python3 -m venv /opt/yaffo-hub/venv
  fi
  sudo /opt/yaffo-hub/venv/bin/pip install --quiet --upgrade /opt/yaffo-hub/src
  sudo chown -R yaffo-hub:yaffo-hub /opt/yaffo-hub
  sudo systemctl restart yaffo-hub
  sleep 2
  sudo systemctl is-active yaffo-hub
  curl -sf http://127.0.0.1:$HUB_PORT/healthz && echo
" --quiet

echo
echo "Verifying the public TLS path (proves DNS + Caddy + cert + proxy)..."
if curl -sf --max-time 15 "https://$DOMAIN/healthz"; then
  echo
  echo "Done: hub healthy at https://$DOMAIN (signaling: wss://$DOMAIN/ws/<device_id>)."
else
  echo "Local healthz passed but https://$DOMAIN/healthz is not reachable yet."
  echo "If the A record was created recently, DNS/ACME may still be propagating — retry in a few minutes."
fi

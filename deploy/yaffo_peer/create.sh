#!/usr/bin/env bash
# Create (or update) the yaffo-peer test VM: a full Yaffo instance on GCP
# for real-internet p2p testing against a local instance. Idempotent and
# doubles as the redeploy path — re-running pushes the current local
# working tree and restarts the service. First run also seeds the media
# dir with yaffo_ui_tests/test_data (SKIP_SEED=1 to skip).
#
# Usage (from deploy/yaffo_peer/): ./create.sh
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

ensure_key

if vm_exists; then
  echo "VM $VM_NAME already exists — redeploying code to it."
  CREATED_VM=false
else
  echo "Creating $VM_NAME ($MACHINE_TYPE, $IMAGE_FAMILY, $ZONE, external IP)..."
  gcloud compute instances create "$VM_NAME" \
    --project "$PROJECT" --zone "$ZONE" \
    --machine-type "$MACHINE_TYPE" \
    --image-family "$IMAGE_FAMILY" --image-project "$IMAGE_PROJECT" \
    --boot-disk-size "$BOOT_DISK_SIZE" \
    --quiet
  CREATED_VM=true

  echo "Adding the automation SSH key to the VM's instance metadata..."
  INSTANCE_KEYS_FILE=$(mktemp)
  echo "$SSH_USER:$(cat "$KEY_PUB")" > "$INSTANCE_KEYS_FILE"
  gcloud compute instances add-metadata "$VM_NAME" \
    --zone "$ZONE" --project "$PROJECT" \
    --metadata-from-file ssh-keys="$INSTANCE_KEYS_FILE" \
    --quiet
  rm -f "$INSTANCE_KEYS_FILE"
fi

IP=$(vm_ip)
echo "  -> external IP: $IP"
wait_for_ssh "$IP"

echo "Packaging the yaffo source (current working tree, not HEAD)..."
TARBALL=$(mktemp -t yaffo_src.XXXXXX.tar.gz)
trap 'rm -f "$TARBALL"' EXIT
# COPYFILE_DISABLE stops macOS bsdtar from smuggling ._AppleDouble files in.
COPYFILE_DISABLE=1 tar czf "$TARBALL" -C "$REPO_ROOT" \
  --exclude "yaffo/__pycache__" --exclude "*.pyc" \
  pyproject.toml VERSION LICENSE README.md yaffo

echo "Copying source + setup script to the VM..."
scp "${SSH_OPTS[@]}" -q "$TARBALL" "$SSH_USER@$IP:/tmp/yaffo_src.tar.gz"
scp "${SSH_OPTS[@]}" -q "$PEER_SCRIPT_DIR/files/remote-setup.sh" "$SSH_USER@$IP:/tmp/yaffo-remote-setup.sh"

echo "Running remote setup (apt + venv + pip install + systemd)..."
ssh "${SSH_OPTS[@]}" "$SSH_USER@$IP" "chmod +x /tmp/yaffo-remote-setup.sh && WEB_PORT=$WEB_PORT /tmp/yaffo-remote-setup.sh"

if [[ "$CREATED_VM" == true && "${SKIP_SEED:-}" != "1" ]]; then
  echo "Seeding the media dir with yaffo_ui_tests/test_data..."
  "$PEER_SCRIPT_DIR/copy-media.sh" "$REPO_ROOT/yaffo_ui_tests/test_data"
fi

cat <<DONE

yaffo-peer is up ($IP). Next steps:
  1. ./port-forward.sh                     # then open the printed URL
  2. In the peer's UI: Settings -> add $REMOTE_MEDIA_DIR as a media
     directory (indexing picks up the seeded files), and set a download
     directory on the Sharing page if you'll pull files TO the peer.
  3. Pair it with your local instance from the Sharing tab.
  4. ./teardown.sh when finished — the VM bills while it exists.

Logs on the VM: ssh -i $KEY $SSH_USER@$IP 'sudo journalctl -u yaffo-peer -f'
(First startup downloads the CLIP/InsightFace models — give it a few minutes.)
DONE

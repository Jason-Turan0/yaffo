# Shared configuration + helpers for the yaffo-peer test VM scripts.
# Sourced by create.sh / copy-media.sh / port-forward.sh / teardown.sh —
# not executable on its own.
#
# This is a TEMPORARY test instance (Phase 6 two-machine validation over
# the real internet), in the p2p-poc/gcp script style: plain idempotent
# gcloud/ssh shell, a dedicated passphrase-less automation key (personal
# keys tend to carry passphrases, which fail silently without a TTY), and
# everything torn down with one command.

PROJECT="gen-lang-client-0392476874"
REGION="us-central1"
ZONE="us-central1-a"
VM_NAME="yaffo-peer"
NAT_VM_NAME="$VM_NAME-nat"
NETWORK_NAME="$VM_NAME-net"
SUBNET_NAME="$VM_NAME-subnet"
SUBNET_RANGE="10.30.0.0/24"
DEVICE_TAG="$VM_NAME-device"
# Indexing runs InsightFace + CLIP on CPU; e2-micro chokes on it.
MACHINE_TYPE="e2-standard-2"
NAT_MACHINE_TYPE="e2-micro"
BOOT_DISK_SIZE="40GB"
# Yaffo pins python ~=3.13.0; Debian 13 (trixie) is the image generation
# whose system python3 is 3.13. remote-setup.sh asserts this loudly.
IMAGE_FAMILY="debian-13"
IMAGE_PROJECT="debian-cloud"
SSH_USER="jason.turan"

# The waitress server binds 127.0.0.1 on the VM, so the UI is reachable
# ONLY through port-forward.sh. In the NAT topology, SSH and tunnels ride
# IAP because the app VM has no external IP.
WEB_PORT=5001
REMOTE_ROOT="/opt/yaffo"
REMOTE_DATA_DIR="$REMOTE_ROOT/data"
REMOTE_MEDIA_DIR="$REMOTE_ROOT/media"

PEER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PEER_SCRIPT_DIR/../.." && pwd)"
KEY="$PEER_SCRIPT_DIR/yaffo-peer-key"
KEY_PUB="$KEY.pub"
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10)
GCLOUD_SSH_OPTS=(--tunnel-through-iap --ssh-key-file="$KEY" --ssh-flag="-o StrictHostKeyChecking=no" --ssh-flag="-o UserKnownHostsFile=/dev/null")
GCLOUD_SCP_OPTS=(--tunnel-through-iap --ssh-key-file="$KEY" --scp-flag="-o StrictHostKeyChecking=no" --scp-flag="-o UserKnownHostsFile=/dev/null")

ensure_key() {
  if [[ ! -f "$KEY" || ! -f "$KEY_PUB" ]]; then
    echo "No automation SSH keypair at $KEY — generating one."
    ssh-keygen -t ed25519 -N "" -f "$KEY" -C "yaffo-peer-automation" -q
  fi
}

vm_exists() {
  gcloud compute instances describe "$VM_NAME" --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1
}

vm_ip() {
  gcloud compute instances describe "$VM_NAME" --zone "$ZONE" --project "$PROJECT" \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
}

vm_internal_ip() {
  gcloud compute instances describe "$VM_NAME" --zone "$ZONE" --project "$PROJECT" \
    --format='get(networkInterfaces[0].networkIP)'
}

vm_has_external_ip() {
  [[ -n "$(vm_ip)" ]]
}

peer_label() {
  local ip
  ip="$(vm_ip)"
  if [[ -n "$ip" ]]; then
    echo "$ip"
  else
    echo "$VM_NAME via IAP ($(vm_internal_ip))"
  fi
}

peer_ssh_host() {
  local ip
  ip="$(vm_ip)"
  if [[ -n "$ip" ]]; then
    echo "$ip"
  else
    echo "$VM_NAME"
  fi
}

peer_ssh() {
  local command="$1"
  local ip
  ip="$(vm_ip)"
  if [[ -n "$ip" ]]; then
    ssh "${SSH_OPTS[@]}" "$SSH_USER@$ip" "$command"
  else
    gcloud compute ssh "$VM_NAME" --zone "$ZONE" --project "$PROJECT" \
      "${GCLOUD_SSH_OPTS[@]}" --command="$command" --quiet
  fi
}

peer_scp_to() {
  local src="$1"
  local dest="$2"
  local ip
  ip="$(vm_ip)"
  if [[ -n "$ip" ]]; then
    scp "${SSH_OPTS[@]}" -q "$src" "$SSH_USER@$ip:$dest"
  else
    gcloud compute scp "$src" "$VM_NAME:$dest" --zone "$ZONE" --project "$PROJECT" \
      "${GCLOUD_SCP_OPTS[@]}" --quiet
  fi
}

peer_tunnel() {
  local local_port="$1"
  local ip
  ip="$(vm_ip)"
  if [[ -n "$ip" ]]; then
    exec ssh "${SSH_OPTS[@]}" -N -L "$local_port:127.0.0.1:$WEB_PORT" "$SSH_USER@$ip"
  else
    exec gcloud compute ssh "$VM_NAME" --zone "$ZONE" --project "$PROJECT" \
      "${GCLOUD_SSH_OPTS[@]}" -- -N -L "$local_port:127.0.0.1:$WEB_PORT"
  fi
}

sshfs_options() {
  local ip
  ip="$(vm_ip)"
  if [[ -n "$ip" ]]; then
    printf '%s\n' \
      -o "IdentityFile=$KEY" \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=10
  else
    return 1
  fi
}

require_vm() {
  if ! vm_exists; then
    echo "VM $VM_NAME does not exist — run ./create.sh first." >&2
    exit 1
  fi
}

wait_for_ssh() {
  local ip="$1"
  echo "Waiting for SSH on $ip..."
  for i in $(seq 1 30); do
    if ssh "${SSH_OPTS[@]}" "$SSH_USER@$ip" "echo ready" >/dev/null 2>&1; then
      echo "  -> SSH is up"
      return 0
    fi
    if [[ "$i" -eq 30 ]]; then
      echo "SSH never came up after ~2.5 minutes — aborting." >&2
      exit 1
    fi
    sleep 5
  done
}

wait_for_gcloud_ssh() {
  local vm="$1"
  echo "Waiting for SSH on $vm via IAP..."
  for i in $(seq 1 30); do
    if gcloud compute ssh "$vm" --zone "$ZONE" --project "$PROJECT" \
      "${GCLOUD_SSH_OPTS[@]}" --command="echo ready" --quiet >/dev/null 2>&1; then
      echo "  -> SSH is up"
      return 0
    fi
    if [[ "$i" -eq 30 ]]; then
      echo "SSH never came up on $vm after ~2.5 minutes — aborting." >&2
      exit 1
    fi
    sleep 5
  done
}

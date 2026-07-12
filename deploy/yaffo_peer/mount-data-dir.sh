#!/usr/bin/env bash
# Mount (or unmount) the yaffo-peer VM's YAFFO_DATA_DIR on this machine via
# sshfs. The VM service sets YAFFO_DATA_DIR=/opt/yaffo/data.
#
# Usage (from deploy/yaffo_peer/):
#   ./mount-data-dir.sh                         # -> /tmp/yaffo-peer-data
#   MOUNT_POINT="$HOME/Desktop/peer-data" ./mount-data-dir.sh
#   ./mount-data-dir.sh unmount
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

MOUNT_POINT="${MOUNT_POINT:-/tmp/yaffo-peer-data}"
ACTION="${1:-mount}"

usage() {
  cat <<USAGE
Usage:
  ./mount-data-dir.sh [mount|unmount]

Environment:
  MOUNT_POINT=/path/to/local/mount  Defaults to /tmp/yaffo-peer-data
USAGE
}

is_mounted() {
  if command -v mountpoint >/dev/null 2>&1; then
    mountpoint -q "$MOUNT_POINT"
    return
  fi

  mount | grep -F " on $MOUNT_POINT " >/dev/null 2>&1
}

unmount_data_dir() {
  if ! is_mounted; then
    echo "$MOUNT_POINT is not mounted."
    return 0
  fi

  if [[ "$(uname -s)" == "Darwin" ]]; then
    umount "$MOUNT_POINT"
  else
    fusermount -u "$MOUNT_POINT"
  fi
  echo "Unmounted $MOUNT_POINT."
}

mount_data_dir() {
  if ! command -v sshfs >/dev/null 2>&1; then
    echo "sshfs is required. On macOS, install macFUSE + sshfs first." >&2
    exit 1
  fi

  ensure_key
  require_vm
  if ! vm_has_external_ip; then
    cat >&2 <<ERROR
sshfs mounting is not currently wired through IAP for the private NAT topology.
Use ./copy-media.sh for file copy, or inspect the VM with:
  gcloud compute ssh $VM_NAME --zone $ZONE --project $PROJECT --tunnel-through-iap --ssh-key-file=$KEY
ERROR
    exit 1
  fi
  mkdir -p "$MOUNT_POINT"

  if is_mounted; then
    echo "$MOUNT_POINT is already mounted."
    return 0
  fi

  local sshfs_args=()
  while IFS= read -r option; do
    sshfs_args+=("$option")
  done < <(sshfs_options)

  echo "Mounting $VM_NAME:$REMOTE_DATA_DIR -> $MOUNT_POINT ..."
  sshfs \
    "${sshfs_args[@]}" \
    -o reconnect \
    "$SSH_USER@$(peer_ssh_host):$REMOTE_DATA_DIR" \
    "$MOUNT_POINT"

  echo "Mounted. Unmount with: ./mount-data-dir.sh unmount"
}

case "$ACTION" in
  mount)
    mount_data_dir
    ;;
  unmount|umount)
    unmount_data_dir
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac

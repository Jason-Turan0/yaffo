#!/usr/bin/env bash
# Copy a local directory's CONTENTS into the yaffo-peer VM's media dir
# ($REMOTE_MEDIA_DIR). Defaults to the UI-test fixture set. Existing files
# with the same relative paths are overwritten; nothing is deleted.
#
# Usage (from deploy/yaffo_peer/):
#   ./copy-media.sh                    # seeds yaffo_ui_tests/test_data
#   ./copy-media.sh ~/Pictures/trip    # -> /opt/yaffo/media/<contents>
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

LOCAL_DIR="${1:-$REPO_ROOT/yaffo_ui_tests/test_data}"
if [[ ! -d "$LOCAL_DIR" ]]; then
  echo "$LOCAL_DIR is not a directory" >&2
  exit 1
fi

ensure_key
require_vm
IP=$(vm_ip)

echo "Copying contents of $LOCAL_DIR -> $VM_NAME:$REMOTE_MEDIA_DIR ..."
# tar over ssh: one round trip, keeps subdirectory structure, and
# COPYFILE_DISABLE stops macOS from adding ._AppleDouble junk that the
# indexer would then try to parse as photos.
COPYFILE_DISABLE=1 tar czf - -C "$LOCAL_DIR" --exclude ".DS_Store" . |
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$IP" "mkdir -p $REMOTE_MEDIA_DIR && tar xzf - -C $REMOTE_MEDIA_DIR"

echo "Done. If $REMOTE_MEDIA_DIR is already a configured media directory,"
echo "the watcher will index the new files; otherwise add it in the peer's"
echo "Settings first (./port-forward.sh to reach the UI)."

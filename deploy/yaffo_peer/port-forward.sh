#!/usr/bin/env bash
# Forward the yaffo-peer web UI to this machine. The peer's waitress server
# binds 127.0.0.1 on the VM (never exposed to the internet), so this SSH
# tunnel is the only way in. Runs in the foreground; Ctrl-C to stop.
#
# Usage (from deploy/yaffo_peer/):
#   ./port-forward.sh                 # http://localhost:5601
#   LOCAL_PORT=7000 ./port-forward.sh # pick another local port
#
# The local default is 5601 (not 5001) so it never collides with a local
# dev instance of yaffo running on this machine.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

LOCAL_PORT="${LOCAL_PORT:-5601}"

ensure_key
require_vm
IP=$(vm_ip)

echo "yaffo-peer UI: http://localhost:$LOCAL_PORT  (tunnel to $IP, Ctrl-C to stop)"
exec ssh "${SSH_OPTS[@]}" -N -L "$LOCAL_PORT:127.0.0.1:$WEB_PORT" "$SSH_USER@$IP"

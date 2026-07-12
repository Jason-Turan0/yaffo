#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

ensure_key
require_vm
peer_ssh "sudo journalctl -u yaffo-peer -f"

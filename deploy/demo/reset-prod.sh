#!/usr/bin/env bash
# On-demand production reset: restore demo-a/demo-b to the golden state
# without waiting for the VM's next scheduled boot. Runs the exact same
# restore-golden.sh the startup script runs on every boot (see
# files/restore-golden.sh.tftpl) — safe to interrupt and re-run, same
# atomic-staging-swap self-heal as deploy/demo/reset-local.sh.
#
# Usage: ./reset-prod.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(terraform -chdir="$script_dir" output -raw project_id)"
vm="$(terraform -chdir="$script_dir" output -raw vm_name)"
zone="$(terraform -chdir="$script_dir" output -raw vm_zone)"
walkthrough_host="$(terraform -chdir="$script_dir" output -raw walkthrough_domain)"
device_a_host="$(terraform -chdir="$script_dir" output -raw demo_a_domain)"
device_b_host="$(terraform -chdir="$script_dir" output -raw demo_b_domain)"

KEY="$script_dir/yaffo-demo-admin-key"
if [[ ! -f "$KEY" ]]; then
    echo "No automation SSH key at $KEY — see deploy.sh for how to generate one." >&2
    exit 1
fi
SSH_ARGS=(--project="$project" --zone="$zone" --tunnel-through-iap --ssh-key-file="$KEY")

echo "Resetting $vm to the golden state..."
gcloud compute ssh "$vm" "${SSH_ARGS[@]}" --quiet --command="sudo /var/lib/yaffo-demo/bin/restore-golden.sh"

echo
echo "Verifying the public HTTPS path for all three hostnames..."
for host in "$walkthrough_host" "$device_a_host" "$device_b_host"; do
    if curl -sf --max-time 15 -o /dev/null "https://$host"; then
        echo "  https://$host — ok"
    else
        echo "  https://$host — not reachable yet (health checks/ACME may still be warming up)"
    fi
done

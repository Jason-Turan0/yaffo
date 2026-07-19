#!/usr/bin/env bash
set -euo pipefail

if [[ ${1:-} != "--confirm" ]]; then
    echo "This disables public demo ingress and stops the VM." >&2
    echo "Re-run as: $0 --confirm" >&2
    exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(terraform -chdir="$script_dir" output -raw project_id)"
vm="$(terraform -chdir="$script_dir" output -raw vm_name)"
zone="$(terraform -chdir="$script_dir" output -raw vm_zone)"
firewall="$(terraform -chdir="$script_dir" output -raw public_firewall_rule)"

gcloud compute firewall-rules update "$firewall" --disabled --project="$project" --quiet
gcloud compute instances stop "$vm" --zone="$zone" --project="$project" --quiet

echo "Public ingress is disabled and $vm is stopped."
echo "A reviewed terraform apply restores the declared ingress rule."


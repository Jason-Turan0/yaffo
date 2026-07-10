#!/usr/bin/env bash
# Tier-2 test stack, DIY-NAT edition. Replaces the Cloud NAT setup from
# nat-punch-setup.sh (see PUNCH_FINDINGS.md for why Cloud NAT was a dead
# end: its filtering behavior isn't controllable, and it's an
# unrepresentative worst case for home routers anyway).
#
# Topology — two simulated "homes" plus one always-on hub:
#
#   home-a VPC (10.10.0.0/24)          home-b VPC (10.20.0.0/24)
#   ┌──────────────────────┐           ┌──────────────────────┐
#   │ device-a (no ext IP) │           │ device-b (no ext IP) │
#   │   default route ↓    │           │   default route ↓    │
#   │ nat-a (ext IP,       │           │ nat-b (ext IP,       │
#   │  iptables MASQUERADE)│           │  iptables MASQUERADE)│
#   └──────────┬───────────┘           └───────────┬──────────┘
#              └────────────► internet ◄───────────┘
#                                ▲
#                    hub VM (ext IP): WebSocket signaling
#                    on tcp:8000 + UDP relay/STUN on udp:40000
#
# Each NAT VM is a faithful model of a consumer router: private subnet,
# one public IP, netfilter masquerade. Crucially the NAT behavior is now a
# TEST PARAMETER instead of an undocumented managed-product property:
#
#   NAT_PROFILE_A/B=punchable  (default) plain MASQUERADE — preserves the
#                              source port when free (endpoint-independent
#                              mapping) with address+port-dependent
#                              filtering, i.e. a port-restricted cone: the
#                              typical home router.
#   NAT_PROFILE_A/B=hard       MASQUERADE --random — a new external port
#                              per flow (endpoint-dependent mapping, like
#                              symmetric NAT / CGNAT).
#
# Expected call outcomes (A calls B) — verified against this stack:
#
#   punchable x punchable  -> path "direct"
#   either side hard       -> path "relay"
#
# Why a single hard side defeats the punch in BOTH directions: netfilter's
# punchable profile is port-restricted (inbound allowed only from an
# ip:port this side has sent to). The hard side sends from a fresh random
# port per flow, so its punch packets arrive from a port the punchable
# side never sent to (dropped), while the punchable side's packets target
# the hard side's STUN port, which belongs to its relay flow (dropped).
# The caller does dial the peer-reflexive address when a punch lands
# (helps against real-world routers with address-only filtering), but
# port-restricted x symmetric is the classic untraversable pairing —
# exactly what the relay fallback exists for.
#
# Both homes share one region: unlike two VMs behind one Cloud NAT
# gateway, these are genuinely separate NAT boxes with their own external
# IPs, so there is no hairpinning problem to dodge.
#
# Idempotent: safe to re-run after a partial failure or after
# tier2-teardown.sh. Usage (from the p2p-poc/ directory):
#
#   ./gcp/tier2-setup.sh
#   NAT_PROFILE_B=hard ./gcp/tier2-setup.sh   # relay-fallback scenario
#
# Then drive a call with ./gcp/tier2-call.sh and clean up with
# ./gcp/tier2-teardown.sh.

set -euo pipefail

PROJECT="gen-lang-client-0392476874"
REGION="us-central1"
ZONE="us-central1-a"
PREFIX="p2p-tier2"
SSH_USER="jason.turan"

NAT_PROFILE_A="${NAT_PROFILE_A:-punchable}"
NAT_PROFILE_B="${NAT_PROFILE_B:-punchable}"

HUB_VM="$PREFIX-hub"
HUB_TAG="$PREFIX-hub"
DEVICE_TAG="$PREFIX-device"
HUB_PORT=8000
RELAY_PORT=40000
DEVICE_PORT=8001

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
KEY="$SCRIPT_DIR/p2p_poc_gce_key"
KEY_PUB="$KEY.pub"
SSH_OPTS=(--tunnel-through-iap --ssh-key-file="$KEY")

if [[ ! -f "$KEY" || ! -f "$KEY_PUB" ]]; then
  echo "No automation SSH keypair at $KEY — generating one."
  ssh-keygen -t ed25519 -N "" -f "$KEY" -C "p2p-poc-automation" -q
fi

exists() { gcloud compute "$1" describe "$2" "${@:3}" --project="$PROJECT" >/dev/null 2>&1; }

# --- one simulated home: VPC + subnet + firewall + NAT VM + routes --------

ensure_home() {
  local name="$1" cidr="$2" nat_profile="$3"
  local network="$PREFIX-$name" nat_vm="$PREFIX-nat-$name"

  if exists networks "$network"; then
    echo "  -> network $network already exists"
  else
    echo "  -> creating network $network..."
    gcloud compute networks create "$network" --project="$PROJECT" --subnet-mode=custom --quiet
    gcloud compute networks subnets create "$network-subnet" \
      --project="$PROJECT" --network="$network" --region="$REGION" --range="$cidr" --quiet
  fi

  # Custom VPCs have no default allow rules at all: open SSH plus the
  # device's HTTPS UI from IAP's published range (the UI port makes
  # `gcloud compute start-iap-tunnel` port-forwarding work — see
  # tier2-ui.sh), and everything inside the home (the device->NAT
  # forwarding path needs it). The rule keeps its historical "-ssh" name
  # so teardown and existing stacks stay consistent.
  if ! exists firewall-rules "$network-allow-iap-ssh"; then
    gcloud compute firewall-rules create "$network-allow-iap-ssh" \
      --project="$PROJECT" --network="$network" --direction=INGRESS --action=ALLOW \
      --rules="tcp:22,tcp:$DEVICE_PORT" --source-ranges=35.235.240.0/20 --quiet
  else
    # Idempotent refresh so stacks created before the UI port was added
    # pick it up on re-run.
    gcloud compute firewall-rules update "$network-allow-iap-ssh" \
      --project="$PROJECT" --rules="tcp:22,tcp:$DEVICE_PORT" --quiet
  fi
  if ! exists firewall-rules "$network-allow-internal"; then
    gcloud compute firewall-rules create "$network-allow-internal" \
      --project="$PROJECT" --network="$network" --direction=INGRESS --action=ALLOW \
      --rules=all --source-ranges="$cidr" --quiet
  fi

  if exists instances "$nat_vm" --zone="$ZONE"; then
    echo "  -> NAT VM $nat_vm already exists"
  else
    echo "  -> creating NAT VM $nat_vm (profile: $nat_profile)..."
    local startup
    startup=$(mktemp)
    cat > "$startup" <<'STARTUP'
#!/bin/bash
set -e
sysctl -w net.ipv4.ip_forward=1
IFACE=$(ip -o -4 route show to default | awk '{print $5}')
PROFILE=$(curl -s -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/nat-profile || echo punchable)
iptables -t nat -F POSTROUTING
if [ "$PROFILE" = "hard" ]; then
  # Endpoint-DEPENDENT mapping (symmetric-NAT-like): a fresh random source
  # port per flow, so the STUN-learned port is useless to the peer and the
  # punch should fail -> the call must stay on the relay.
  iptables -t nat -A POSTROUTING -o "$IFACE" -j MASQUERADE --random
else
  # Plain masquerade: netfilter preserves the source port when free
  # (endpoint-independent mapping) and filters per address+port -- a
  # port-restricted cone, the typical punchable home router.
  iptables -t nat -A POSTROUTING -o "$IFACE" -j MASQUERADE
fi
iptables -P FORWARD ACCEPT
STARTUP
    gcloud compute instances create "$nat_vm" \
      --project="$PROJECT" --zone="$ZONE" \
      --machine-type=e2-micro \
      --image-family=debian-12 --image-project=debian-cloud \
      --network-interface="network=$network,subnet=$network-subnet" \
      --can-ip-forward \
      --metadata="nat-profile=$nat_profile" \
      --metadata-from-file=startup-script="$startup" \
      --quiet
    rm -f "$startup"
  fi

  # Tagged device VMs route everything through the NAT VM (priority 800
  # beats the auto-created 0/0 default-internet-gateway route at 1000)...
  if ! exists routes "$network-via-nat"; then
    gcloud compute routes create "$network-via-nat" \
      --project="$PROJECT" --network="$network" \
      --destination-range=0.0.0.0/0 --priority=800 \
      --next-hop-instance="$nat_vm" --next-hop-instance-zone="$ZONE" \
      --tags="$DEVICE_TAG" --quiet
  fi
  # ...except IAP's range, or SSH return traffic would get masqueraded and
  # break the tunnel (more-specific prefix wins regardless of priority).
  if ! exists routes "$network-iap-direct"; then
    gcloud compute routes create "$network-iap-direct" \
      --project="$PROJECT" --network="$network" \
      --destination-range=35.235.240.0/20 --priority=100 \
      --next-hop-gateway=default-internet-gateway --quiet
  fi
}

add_ssh_key() {
  local vm="$1"
  local keys_file
  keys_file=$(mktemp)
  echo "$SSH_USER:$(cat "$KEY_PUB")" > "$keys_file"
  gcloud compute instances add-metadata "$vm" --zone="$ZONE" --project="$PROJECT" \
    --metadata-from-file ssh-keys="$keys_file" --quiet
  rm -f "$keys_file"
}

# The startup script only runs at boot, so an existing NAT VM would keep
# whatever profile it was created with no matter what NAT_PROFILE_* says.
# Re-apply the iptables rules over SSH on EVERY run so switching profiles
# is just a re-run of this script, no VM recreation needed.
apply_nat_profile() {
  local name="$1" nat_profile="$2"
  local nat_vm="$PREFIX-nat-$name"
  echo "  -> applying NAT profile '$nat_profile' on $nat_vm..."
  add_ssh_key "$nat_vm"  # NAT VMs from older stack versions may lack the automation key
  gcloud compute instances add-metadata "$nat_vm" --zone="$ZONE" --project="$PROJECT" \
    --metadata="nat-profile=$nat_profile" --quiet
  local random_flag=""
  [[ "$nat_profile" == "hard" ]] && random_flag="--random"
  gcloud compute ssh "$nat_vm" --zone="$ZONE" --project="$PROJECT" "${SSH_OPTS[@]}" --command="
    set -e
    sudo sysctl -qw net.ipv4.ip_forward=1
    IFACE=\$(ip -o -4 route show to default | awk '{print \$5}')
    sudo iptables -t nat -F POSTROUTING
    sudo iptables -t nat -A POSTROUTING -o \"\$IFACE\" -j MASQUERADE $random_flag
    sudo iptables -P FORWARD ACCEPT
    # Old conntrack entries keep their old mappings until they expire, which
    # would blur the profile switch — drop UDP state (best effort).
    sudo apt-get install -y -qq conntrack >/dev/null 2>&1 || true
    sudo conntrack -D -p udp >/dev/null 2>&1 || true
    echo \"profile '$nat_profile' active\"
  " --quiet 2>&1 | grep -v "^WARNING:" || true
}

ensure_device_vm() {
  local name="$1"
  local network="$PREFIX-$name" vm="$PREFIX-device-$name"
  if exists instances "$vm" --zone="$ZONE"; then
    echo "  -> $vm already exists"
    return
  fi
  echo "  -> creating device VM $vm (no external IP, routed via NAT VM)..."
  gcloud compute instances create "$vm" \
    --project="$PROJECT" --zone="$ZONE" \
    --machine-type=e2-micro \
    --image-family=debian-12 --image-project=debian-cloud \
    --network-interface="network=$network,subnet=$network-subnet,no-address" \
    --tags="$DEVICE_TAG" --quiet
  add_ssh_key "$vm"
}

ensure_hub_vm() {
  if ! exists firewall-rules "$PREFIX-hub-ingress"; then
    gcloud compute firewall-rules create "$PREFIX-hub-ingress" \
      --project="$PROJECT" --network=default --direction=INGRESS --action=ALLOW \
      --rules="tcp:$HUB_PORT,udp:$RELAY_PORT" --source-ranges=0.0.0.0/0 \
      --target-tags="$HUB_TAG" --quiet
  fi
  if ! exists firewall-rules "$PREFIX-hub-iap-ssh"; then
    gcloud compute firewall-rules create "$PREFIX-hub-iap-ssh" \
      --project="$PROJECT" --network=default --direction=INGRESS --action=ALLOW \
      --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags="$HUB_TAG" --quiet
  fi
  if exists instances "$HUB_VM" --zone="$ZONE"; then
    echo "  -> $HUB_VM already exists"
    return
  fi
  echo "  -> creating hub VM $HUB_VM (external IP — the one dialable box)..."
  gcloud compute instances create "$HUB_VM" \
    --project="$PROJECT" --zone="$ZONE" \
    --machine-type=e2-micro \
    --image-family=debian-12 --image-project=debian-cloud \
    --tags="$HUB_TAG" --quiet
  add_ssh_key "$HUB_VM"
}

wait_for_ssh() {
  local vm="$1"
  echo "  -> waiting for SSH on $vm..."
  for i in $(seq 1 30); do
    if gcloud compute ssh "$vm" --zone="$ZONE" --project="$PROJECT" "${SSH_OPTS[@]}" --command="echo ready" >/dev/null 2>&1; then
      echo "  -> SSH is up"
      return 0
    fi
    if [[ "$i" -eq 30 ]]; then
      echo "SSH never came up for $vm after ~2.5 minutes"
      exit 1
    fi
    sleep 5
  done
}

deploy_code() {
  local vm="$1"
  echo "  -> setting up Python environment on $vm..."
  gcloud compute ssh "$vm" --zone="$ZONE" --project="$PROJECT" "${SSH_OPTS[@]}" --command="
    mkdir -p ~/p2p-poc
    if [ ! -d ~/p2p-poc/venv ]; then
      sudo apt-get update -qq
      sudo apt-get install -y -qq python3-venv python3-pip >/dev/null
      python3 -m venv ~/p2p-poc/venv
    fi
  " --quiet >/dev/null 2>&1

  echo "  -> copying code to $vm..."
  local tarball
  tarball=$(mktemp -t p2p_poc_code.XXXXXX.tar.gz)
  tar czf "$tarball" -C "$REPO_ROOT" p2p_poc requirements.txt
  gcloud compute scp "$tarball" "$vm:~/p2p_poc_code.tar.gz" --zone="$ZONE" --project="$PROJECT" "${SSH_OPTS[@]}" --quiet >/dev/null 2>&1
  rm -f "$tarball"

  gcloud compute ssh "$vm" --zone="$ZONE" --project="$PROJECT" "${SSH_OPTS[@]}" --command="
    set -e
    rm -rf ~/p2p-poc/p2p_poc
    tar xzf ~/p2p_poc_code.tar.gz -C ~/p2p-poc
    cd ~/p2p-poc
    source venv/bin/activate
    pip install -q -r requirements.txt
  " --quiet 2>&1 | grep -v "^WARNING:\|NumPy\|increasing_the_tcp\|^tar: Ignoring" || true
}

start_service() {
  local vm="$1" command="$2" check="$3"
  echo "  -> starting service on $vm..."
  gcloud compute ssh "$vm" --zone="$ZONE" --project="$PROJECT" "${SSH_OPTS[@]}" --command="
    set -e
    cd ~/p2p-poc
    source venv/bin/activate
    if [ -f ~/service.pid ]; then kill \"\$(cat ~/service.pid)\" 2>/dev/null || true; rm -f ~/service.pid; sleep 1; fi
    nohup $command < /dev/null > ~/service.log 2>&1 &
    echo \$! > ~/service.pid
    sleep 2
    $check
  " --quiet 2>&1 | grep -v "^WARNING:" || true
}

echo "Ensuring home A (NAT profile: $NAT_PROFILE_A)..."
ensure_home a 10.10.0.0/24 "$NAT_PROFILE_A"
echo "Ensuring home B (NAT profile: $NAT_PROFILE_B)..."
ensure_home b 10.20.0.0/24 "$NAT_PROFILE_B"
echo "Ensuring hub VM..."
ensure_hub_vm
echo "Ensuring device VMs..."
ensure_device_vm a
ensure_device_vm b

echo "Applying NAT profiles (live, every run — switching profiles is just a re-run)..."
wait_for_ssh "$PREFIX-nat-a"
apply_nat_profile a "$NAT_PROFILE_A"
wait_for_ssh "$PREFIX-nat-b"
apply_nat_profile b "$NAT_PROFILE_B"

wait_for_ssh "$HUB_VM"
wait_for_ssh "$PREFIX-device-a"
wait_for_ssh "$PREFIX-device-b"

HUB_IP=$(gcloud compute instances describe "$HUB_VM" --zone="$ZONE" --project="$PROJECT" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
echo "Hub external IP: $HUB_IP"

echo "Deploying hub..."
deploy_code "$HUB_VM"
start_service "$HUB_VM" \
  "python -m p2p_poc.hub --host 0.0.0.0 --port $HUB_PORT --relay-port $RELAY_PORT" \
  "curl -s http://127.0.0.1:$HUB_PORT/devices"
echo

for side in a b; do
  vm="$PREFIX-device-$side"
  echo "Deploying device $side..."
  deploy_code "$vm"
  start_service "$vm" \
    "python -m p2p_poc.main --host 0.0.0.0 --bind-host 0.0.0.0 --port $DEVICE_PORT --data-dir ~/data/device --hub ws://$HUB_IP:$HUB_PORT" \
    "curl -sk https://127.0.0.1:$DEVICE_PORT/api/device-info"
  echo
done

cat <<EOF

Done. NAT profiles: A=$NAT_PROFILE_A, B=$NAT_PROFILE_B. Drivers:

  ./gcp/tier2-call.sh    one relay-first call, punch upgrade attempt
  ./gcp/tier2-pair.sh    full pairing flow (trust exchange via hub)
  ./gcp/tier2-share.sh   B pulls A's shared text files (pair first)
  ./gcp/tier2-ui.sh      browse a device's web UI from your laptop

Expected outcome (A calls B):
  punchable x punchable -> "path": "direct"
  either side hard      -> "path": "relay" (punch fails, call still works —
                           port-restricted x symmetric is untraversable;
                           see the outcome notes in this script's header)

Tear down with ./gcp/tier2-teardown.sh — this stack is ephemeral by design.
EOF

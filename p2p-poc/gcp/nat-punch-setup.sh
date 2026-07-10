#!/usr/bin/env bash
# Provisions the two-independent-NAT hole-punching test setup: two VMs, each
# with NO external IP, each behind its OWN regional Cloud NAT gateway (so
# they have genuinely different external IPs — critical, since two VMs
# sharing one Cloud NAT gateway hit NAT hairpinning instead of testing real
# inter-NAT punching). See PUNCH_FINDINGS.md for what this proved and what
# it didn't.
#
# Idempotent: safe to re-run after a partial failure or after teardown.sh.
#
# Usage: ./gcp/nat-punch-setup.sh   (run from the p2p-poc/ directory)

set -euo pipefail

PROJECT="gen-lang-client-0392476874"
FIREWALL_TAG="p2p-poc-nat-device"
IAP_SSH_RULE="p2p-poc-nat-allow-iap-ssh"
RENDEZVOUS="https://p2p-poc-rendezvous-16676249361.us-central1.run.app"
SSH_USER="jason.turan"

# region, zone, router, nat-gateway, vm-name
DEVICE_A_REGION="us-central1"; DEVICE_A_ZONE="us-central1-a"
DEVICE_A_ROUTER="p2p-poc-nat-router"; DEVICE_A_GATEWAY="p2p-poc-nat-gateway"; DEVICE_A_VM="p2p-poc-nat-device-a"

DEVICE_B_REGION="us-east1"; DEVICE_B_ZONE="us-east1-b"
DEVICE_B_ROUTER="p2p-poc-nat-router-east"; DEVICE_B_GATEWAY="p2p-poc-nat-gateway-east"; DEVICE_B_VM="p2p-poc-nat-device-b2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
KEY="$SCRIPT_DIR/p2p_poc_gce_key"
KEY_PUB="$KEY.pub"
SSH_OPTS=(--tunnel-through-iap --ssh-key-file="$KEY")

if [[ ! -f "$KEY" || ! -f "$KEY_PUB" ]]; then
  echo "No automation SSH keypair at $KEY — generating one."
  ssh-keygen -t ed25519 -N "" -f "$KEY" -C "p2p-poc-automation" -q
fi

ensure_nat_gateway() {
  local region="$1" router="$2" gateway="$3"
  if gcloud compute routers describe "$router" --region="$region" --project="$PROJECT" >/dev/null 2>&1; then
    echo "  -> router $router already exists"
  else
    echo "  -> creating router $router in $region..."
    gcloud compute routers create "$router" --network=default --region="$region" --project="$PROJECT" --quiet
  fi
  if gcloud compute routers nats describe "$gateway" --router="$router" --region="$region" --project="$PROJECT" >/dev/null 2>&1; then
    echo "  -> NAT gateway $gateway already exists"
  else
    echo "  -> creating NAT gateway $gateway (endpoint-independent mapping — required for punching)..."
    gcloud compute routers nats create "$gateway" \
      --router="$router" --region="$region" --project="$PROJECT" \
      --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges \
      --enable-endpoint-independent-mapping --quiet
  fi
}

ensure_vm() {
  local zone="$1" vm="$2"
  if gcloud compute instances describe "$vm" --zone="$zone" --project="$PROJECT" >/dev/null 2>&1; then
    echo "  -> $vm already exists"
  else
    echo "  -> creating $vm in $zone (no external IP)..."
    gcloud compute instances create "$vm" \
      --project="$PROJECT" --zone="$zone" \
      --machine-type=e2-micro \
      --image-family=debian-12 --image-project=debian-cloud \
      --no-address --tags="$FIREWALL_TAG" --quiet
    echo "  -> adding automation SSH key to instance metadata..."
    local keys_file; keys_file=$(mktemp)
    echo "$SSH_USER:$(cat "$KEY_PUB")" > "$keys_file"
    gcloud compute instances add-metadata "$vm" --zone="$zone" --project="$PROJECT" \
      --metadata-from-file ssh-keys="$keys_file" --quiet
    rm -f "$keys_file"
  fi
}

wait_for_ssh() {
  local zone="$1" vm="$2"
  echo "  -> waiting for SSH..."
  for i in $(seq 1 30); do
    if gcloud compute ssh "$vm" --zone="$zone" --project="$PROJECT" "${SSH_OPTS[@]}" --command="echo ready" >/dev/null 2>&1; then
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

deploy_and_start() {
  local zone="$1" vm="$2"
  echo "  -> setting up Python environment..."
  gcloud compute ssh "$vm" --zone="$zone" --project="$PROJECT" "${SSH_OPTS[@]}" --command="
    mkdir -p ~/p2p-poc
    if [ ! -d ~/p2p-poc/venv ]; then
      sudo apt-get update -qq
      sudo apt-get install -y -qq python3-venv python3-pip >/dev/null
      python3 -m venv ~/p2p-poc/venv
    fi
  " --quiet >/dev/null 2>&1

  echo "  -> copying code..."
  local tarball; tarball=$(mktemp -t p2p_poc_code.XXXXXX.tar.gz)
  tar czf "$tarball" -C "$REPO_ROOT" p2p_poc requirements.txt
  gcloud compute scp "$tarball" "$vm:~/p2p_poc_code.tar.gz" --zone="$zone" --project="$PROJECT" "${SSH_OPTS[@]}" --quiet >/dev/null 2>&1
  rm -f "$tarball"

  echo "  -> installing deps + starting device..."
  gcloud compute ssh "$vm" --zone="$zone" --project="$PROJECT" "${SSH_OPTS[@]}" --command="
    set -e
    rm -rf ~/p2p-poc/p2p_poc
    tar xzf ~/p2p_poc_code.tar.gz -C ~/p2p-poc
    cd ~/p2p-poc
    source venv/bin/activate
    pip install -q -r requirements.txt
    if [ -f ~/device.pid ]; then kill \"\$(cat ~/device.pid)\" 2>/dev/null || true; rm -f ~/device.pid; sleep 1; fi
    nohup python -m p2p_poc.main --host 0.0.0.0 --bind-host 0.0.0.0 --port 8001 \
      --data-dir ~/data/device --rendezvous $RENDEZVOUS \
      < /dev/null > ~/device.log 2>&1 &
    echo \$! > ~/device.pid
    sleep 2
    curl -sk https://127.0.0.1:8001/api/device-info
  " --quiet 2>&1 | grep -v "^WARNING:\|NumPy\|increasing_the_tcp\|^tar: Ignoring"
}

echo "Ensuring IAP SSH firewall rule..."
if gcloud compute firewall-rules describe "$IAP_SSH_RULE" --project="$PROJECT" >/dev/null 2>&1; then
  echo "  -> already exists"
else
  gcloud compute firewall-rules create "$IAP_SSH_RULE" \
    --project="$PROJECT" --direction=INGRESS --action=ALLOW \
    --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags="$FIREWALL_TAG" --quiet
fi

echo "Ensuring Cloud NAT gateway A ($DEVICE_A_REGION)..."
ensure_nat_gateway "$DEVICE_A_REGION" "$DEVICE_A_ROUTER" "$DEVICE_A_GATEWAY"
echo "Ensuring Cloud NAT gateway B ($DEVICE_B_REGION)..."
ensure_nat_gateway "$DEVICE_B_REGION" "$DEVICE_B_ROUTER" "$DEVICE_B_GATEWAY"

echo "Ensuring device A VM..."
ensure_vm "$DEVICE_A_ZONE" "$DEVICE_A_VM"
echo "Ensuring device B VM..."
ensure_vm "$DEVICE_B_ZONE" "$DEVICE_B_VM"

echo "Waiting for device A SSH..."
wait_for_ssh "$DEVICE_A_ZONE" "$DEVICE_A_VM"
echo "Waiting for device B SSH..."
wait_for_ssh "$DEVICE_B_ZONE" "$DEVICE_B_VM"

echo "Deploying device A..."
deploy_and_start "$DEVICE_A_ZONE" "$DEVICE_A_VM"
echo
echo "Deploying device B..."
deploy_and_start "$DEVICE_B_ZONE" "$DEVICE_B_VM"
echo

cat <<'EOF'

Done. To attempt a punch, grab each device_id from the /api/device-info
output above, then fire both of these within a few seconds of each other
(two separate terminals — timing matters, see PUNCH_FINDINGS.md):

  gcloud compute ssh p2p-poc-nat-device-a --zone=us-central1-a \
    --project=gen-lang-client-0392476874 --tunnel-through-iap \
    --ssh-key-file=gcp/p2p_poc_gce_key \
    --command='curl -sk -X POST https://127.0.0.1:8001/api/punch -H "Content-Type: application/json" -d "{\"peer_device_id\":\"<DEVICE_B_ID>\",\"duration\":25}"'

  gcloud compute ssh p2p-poc-nat-device-b2 --zone=us-east1-b \
    --project=gen-lang-client-0392476874 --tunnel-through-iap \
    --ssh-key-file=gcp/p2p_poc_gce_key \
    --command='curl -sk -X POST https://127.0.0.1:8001/api/punch -H "Content-Type: application/json" -d "{\"peer_device_id\":\"<DEVICE_A_ID>\",\"duration\":25}"'

Per PUNCH_FINDINGS.md, this is expected to fail against Cloud NAT's
filtering behavior — the point of rerunning it would be to test a fix
attempt (e.g. after adding a TURN relay), not to get a different result
from the same setup.
EOF
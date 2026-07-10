#!/usr/bin/env bash
# Deploys the current local p2p_poc/ code to the GCP test VM (device A) and
# (re)starts it. Fully self-sufficient: creates the VM, its firewall rules,
# and the automation SSH key if any of them are missing — so this works both
# as the very first deploy and as an ongoing redeploy after a code change,
# including after ./gcp/teardown.sh deleted everything.
#
# Usage: ./gcp/redeploy.sh   (run from the p2p-poc/ directory)

set -euo pipefail

PROJECT="gen-lang-client-0392476874"
ZONE="us-central1-a"
VM_NAME="p2p-poc-device-a"
MACHINE_TYPE="e2-micro"
FIREWALL_TAG="p2p-poc-device"
TCP_FIREWALL_RULE="p2p-poc-device-allow"
UDP_FIREWALL_RULE="p2p-poc-device-allow-udp"
PORT=8001
DATA_DIR="~/data/device-a"
RENDEZVOUS="https://p2p-poc-rendezvous-16676249361.us-central1.run.app"
SSH_USER="jason.turan"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"          # .../p2p-poc
KEY="$SCRIPT_DIR/p2p_poc_gce_key"
KEY_PUB="$KEY.pub"
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10)

if [[ ! -f "$KEY" || ! -f "$KEY_PUB" ]]; then
  echo "No automation SSH keypair at $KEY — generating one (dedicated, passphrase-less;"
  echo "not your personal ~/.ssh key, which can't be used non-interactively)."
  ssh-keygen -t ed25519 -N "" -f "$KEY" -C "p2p-poc-automation" -q
fi

ensure_firewall_rule() {
  local name="$1" rule="$2"
  if gcloud compute firewall-rules describe "$name" --project "$PROJECT" >/dev/null 2>&1; then
    echo "  -> $name already exists"
  else
    echo "  -> creating $name ($rule)..."
    gcloud compute firewall-rules create "$name" \
      --project "$PROJECT" \
      --direction=INGRESS --action=ALLOW \
      --rules="$rule" \
      --source-ranges=0.0.0.0/0 \
      --target-tags="$FIREWALL_TAG" \
      --quiet
  fi
}

echo "Ensuring firewall rules exist..."
ensure_firewall_rule "$TCP_FIREWALL_RULE" "tcp:$PORT"
ensure_firewall_rule "$UDP_FIREWALL_RULE" "udp:$PORT"

CREATED_VM=false
if gcloud compute instances describe "$VM_NAME" --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1; then
  echo "VM $VM_NAME already exists."
else
  echo "VM $VM_NAME doesn't exist — creating it ($MACHINE_TYPE, $ZONE, with an external IP)..."
  gcloud compute instances create "$VM_NAME" \
    --project "$PROJECT" --zone "$ZONE" \
    --machine-type "$MACHINE_TYPE" \
    --image-family debian-12 --image-project debian-cloud \
    --tags "$FIREWALL_TAG" \
    --quiet
  CREATED_VM=true

  echo "Adding the automation SSH key to the new VM's instance metadata..."
  INSTANCE_KEYS_FILE=$(mktemp)
  echo "$SSH_USER:$(cat "$KEY_PUB")" > "$INSTANCE_KEYS_FILE"
  gcloud compute instances add-metadata "$VM_NAME" \
    --zone "$ZONE" --project "$PROJECT" \
    --metadata-from-file ssh-keys="$INSTANCE_KEYS_FILE" \
    --quiet
  rm -f "$INSTANCE_KEYS_FILE"
fi

echo "Looking up $VM_NAME's current external IP..."
VM_IP=$(gcloud compute instances describe "$VM_NAME" --zone "$ZONE" --project "$PROJECT" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
echo "  -> $VM_IP"

echo "Waiting for SSH..."
for i in $(seq 1 30); do
  if ssh "${SSH_OPTS[@]}" "$SSH_USER@$VM_IP" "echo ready" >/dev/null 2>&1; then
    echo "  -> SSH is up"
    break
  fi
  if [[ "$i" -eq 30 ]]; then
    echo "SSH never came up after ~2.5 minutes — aborting."
    exit 1
  fi
  sleep 5
done

echo "Ensuring base packages + venv exist on the VM (skipped if already set up)..."
ssh "${SSH_OPTS[@]}" "$SSH_USER@$VM_IP" "
  set -e
  mkdir -p ~/p2p-poc
  if [ ! -d ~/p2p-poc/venv ]; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-venv python3-pip >/dev/null
    python3 -m venv ~/p2p-poc/venv
  fi
"

echo "Packaging p2p_poc/ + requirements.txt..."
TARBALL=$(mktemp -t p2p_poc_code.XXXXXX.tar.gz)
trap 'rm -f "$TARBALL"' EXIT
tar czf "$TARBALL" -C "$REPO_ROOT" p2p_poc requirements.txt

echo "Copying to the VM..."
scp "${SSH_OPTS[@]}" "$TARBALL" "$SSH_USER@$VM_IP:~/p2p_poc_code.tar.gz"

echo "Installing deps and updating code on the VM..."
ssh "${SSH_OPTS[@]}" "$SSH_USER@$VM_IP" "
  set -e
  rm -rf ~/p2p-poc/p2p_poc
  tar xzf ~/p2p_poc_code.tar.gz -C ~/p2p-poc
  cd ~/p2p-poc
  source venv/bin/activate
  pip install -q -r requirements.txt
"

# Deliberately NOT 'pkill -f p2p_poc.main' (even with the classic [p]
# bracket trick — see redeploy history/commit log): pkill matches a
# process's FULL command line, and since the restart command below (which
# necessarily contains the literal text 'p2p_poc.main', it's what we're
# launching) lives in the very same remote shell invocation as any pkill
# call before it, pkill ends up matching and killing its OWN parent shell
# mid-script — reproducible even with zero real target process running.
# A PID file sidesteps the whole footgun instead of trying to out-clever it.
echo "Stopping any previous device A process (via PID file, not pattern matching)..."
ssh "${SSH_OPTS[@]}" "$SSH_USER@$VM_IP" "
  if [ -f ~/device-a.pid ]; then
    kill \"\$(cat ~/device-a.pid)\" 2>/dev/null || true
    rm -f ~/device-a.pid
    sleep 1
  fi
"

echo "Starting device A on the VM..."
ssh "${SSH_OPTS[@]}" "$SSH_USER@$VM_IP" "
  set -e
  cd ~/p2p-poc
  source venv/bin/activate
  nohup python -m p2p_poc.main --host $VM_IP --bind-host 0.0.0.0 --port $PORT \
    --data-dir $DATA_DIR \
    --rendezvous $RENDEZVOUS \
    < /dev/null > ~/device-a.log 2>&1 &
  echo \$! > ~/device-a.pid
"

echo "Waiting for it to come back up..."
sleep 3
ssh "${SSH_OPTS[@]}" "$SSH_USER@$VM_IP" "cat ~/device-a.log"

echo
echo "Verifying from here (over the real public internet)..."
python3 -c "
import http.client, ssl
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
conn = http.client.HTTPSConnection('$VM_IP', $PORT, timeout=10, context=ctx)
conn.request('GET', '/api/device-info')
resp = conn.getresponse()
print(resp.status, resp.read().decode())
"

echo
echo "Done. Device A live at https://$VM_IP:$PORT (device-info check above should show 200)."
if [[ "$CREATED_VM" == "true" ]]; then
  echo "This VM was just created — it now bills until you run ./gcp/teardown.sh."
fi

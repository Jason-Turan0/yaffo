# Live public-IP test deployment

State from the public-IP pairing test, kept running so you can poke at it
further (e.g. pair from your phone later). See [`teardown.sh`](teardown.sh)
when you're done with it.

Already redeployed with the QUIC/UDP rewrite via `./gcp/redeploy.sh` (both
`tcp:8001` and `udp:8001` firewall rules exist, `aioquic` installed). If it's
been a while, `./gcp/redeploy.sh` is idempotent and safe to rerun to
resync with local changes.

## NAT hole-punching POC (separate, currently torn down)

A second, separate GCP setup — two VMs with no external IP, each behind its
own regional Cloud NAT gateway — was used to test real UDP hole-punching
between two independent NATs. **See
[`PUNCH_FINDINGS.md`](PUNCH_FINDINGS.md)** for the full result: the
STUN/rendezvous/coordination mechanism all work correctly, but the actual
punch is blocked by Cloud NAT's filtering behavior specifically (not a
general NAT problem — see the findings doc for the RFC 4787 mapping-vs-filtering
distinction and the evidence). That infrastructure has been torn down
(`./gcp/nat-punch-teardown.sh` was run) since it doesn't bill for anything
while stopped. To pick this back up: `./gcp/nat-punch-setup.sh` rebuilds it
from scratch (idempotent, ~2 VMs + 2 Cloud NAT gateways + 1 firewall rule),
independent of everything else in this file.

## What's currently live

| Component | Where | Address |
| --- | --- | --- |
| Rendezvous service | Cloud Run (`gen-lang-client-0392476874`, `us-central1`) | `https://p2p-poc-rendezvous-16676249361.us-central1.run.app` |
| Device A | GCE VM `p2p-poc-device-a` (`us-central1-a`, `e2-micro`) | `https://35.225.95.172:8001` — Device ID `DFAO-7WIM-VT44-G5JP` |

Device A is already running (`nohup`'d on the VM) and already registered with
rendezvous. It'll keep running until the VM is stopped/deleted or you SSH in
and kill it.

## Test pairing yourself (browser)

1. On your laptop, start a second device, pointed at the same rendezvous:

   ```bash
   cd p2p-poc && source venv/bin/activate
   python -m p2p_poc.main --port 8021 --data-dir ./data/device-b-vm-test \
     --rendezvous https://p2p-poc-rendezvous-16676249361.us-central1.run.app
   ```

2. Open **`https://35.225.95.172:8001`** in your browser — this is device A,
   running on the VM. Click through the self-signed cert warning (expected —
   same as the local demo). Click **Generate pairing code**, copy it.

3. Open **`https://127.0.0.1:8021`** — your laptop's device B. Click through
   its cert warning too. Paste the code into **Pair with another device**,
   click **Pair**.

4. B's page reloads showing device A in **Known devices**. Reload A's page
   (`https://35.225.95.172:8001`) — it now shows B too.

Notice what *didn't* need to happen: no port forwarding on your home router,
no manual IP entry (rendezvous resolved A's address), and your laptop was
never dialed *into* — it only ever dialed out, both to rendezvous and to A.

## Test pairing yourself (curl / no browser)

```bash
RENDEZVOUS="https://p2p-poc-rendezvous-16676249361.us-central1.run.app"

# Generate a code on the VM's device A
CODE=$(curl -sk -X POST https://35.225.95.172:8001/api/pairing/generate | python3 -c "import sys,json; print(json.load(sys.stdin)['code'])")

# Accept it on your local device B (must already be running — see step 1 above)
curl -sk -X POST https://127.0.0.1:8021/api/pairing/accept \
  -H "Content-Type: application/json" -d "{\"code\": \"$CODE\"}"

# Check both sides
curl -sk https://127.0.0.1:8021/api/known-devices
curl -sk https://35.225.95.172:8001/api/known-devices
```

Note: plain system `curl` works fine here — unlike the earlier Chrome/Ed25519
saga, this cert is ECDSA P-256, which every TLS stack accepts.

## Redeploying device A after a change

```bash
cd p2p-poc
./gcp/redeploy.sh
```

What it does: looks up the VM's current external IP (doesn't assume it hasn't
changed), makes sure the UDP firewall rule exists (creates it if not),
packages `p2p_poc/` + `requirements.txt`, copies it over, reinstalls
dependencies, restarts device A, and verifies it's actually reachable
afterward by hitting `/api/device-info` from here over the real public
internet. Uses the same dedicated, passphrase-less SSH key
(`gcp/p2p_poc_gce_key`, gitignored) added to the VM's instance metadata
during initial setup — not your personal `~/.ssh` key, which is
passphrase-protected and can't be used non-interactively.

Note it passes `--bind-host 0.0.0.0` — a GCE VM's external IP is a NAT
mapping, not an address actually present on the VM's network interface, so
binding directly to it fails (`cannot assign requested address`). `--host` is
what gets advertised (pairing codes, rendezvous, the TLS cert's SAN);
`--bind-host` is what the sockets actually bind to. See
`p2p_poc/main.py --help`.

## Tearing down

```bash
./gcp/teardown.sh
```

Deletes the VM and its firewall rule. Leaves the Cloud Run rendezvous service
and its Firestore data alone (cheap, serverless, worth keeping for future
tests — redeploy device A onto a fresh VM later and it'll still work against
the same rendezvous URL).

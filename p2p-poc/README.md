# P2P Device Pairing — Proof of Concept

A working implementation of the pairing mechanism sketched in
[`docs/development/p2p-sharing.md`](../docs/development/p2p-sharing.md): two
devices establish mutual trust with no CA, no account/login system, and no
server that ever sees photo data. This is the crypto/protocol core plus a
minimal rendezvous/discovery service (see **What this skips** below for what
still isn't here — mDNS and real STUN/TURN NAT traversal).

## What it proves

- **Identity is a keypair, not a login.** Each device generates an ECDSA
  P-256 keypair on first run and derives a short `device_id` from it.
- **TLS cert pinning instead of a CA.** When device B connects to device A to
  complete a pairing, it verifies A's certificate fingerprint against the
  `device_id` from the pairing code *before* sending anything — chain
  validation is disabled on purpose, since there's no CA to validate against.
  A mismatch (simulated MITM) is rejected and never reaches the app layer.
- **Proof of possession, not just a claim.** B signs the pairing code's nonce
  with its own private key; A verifies that signature before trusting B. A
  request merely *claiming* to be a given `device_id` without the matching
  signature is rejected.
- **Pairing codes are single-use and expire** (5 minutes), and are consumed
  from a pending-pairing map on first use.

## Transport: the browser UI is TCP, the actual pairing exchange is UDP

Split deliberately down the middle, because they're different concerns. The
browser UI (`index.html`, the buttons a human clicks) is served over ordinary
HTTPS/TCP via FastAPI/uvicorn, unchanged — there's no reason to fight
browsers over custom QUIC certs just to show a page. The *actual*
device-to-device pairing exchange (`p2p_poc/quic_transport.py`) runs over
**QUIC/UDP** via [`aioquic`](https://github.com/aiortc/aioquic) instead —
this is the part that would sit behind real NAT hole-punching in a full
deployment (see the earlier hole-punching discussion — UDP is what makes that
technique viable at all). Both servers run in the same process, sharing one
port number across two independent protocol namespaces (`lsof` shows the
process holding both `TCP 127.0.0.1:8001` and `UDP 127.0.0.1:8001`
simultaneously) — the FastAPI lifespan starts the QUIC server alongside
uvicorn on app startup.

The same cert-pinning trust model carries over unchanged: QUIC has TLS 1.3
built directly into its handshake, so the identical ECDSA P-256
identity/cert from `identity.py` is reused, and `quic_pinned_confirm()`
pins the peer's certificate fingerprint exactly like the old TCP
`pinned_post()` did — just reading it from `aioquic`'s (undocumented,
private) `protocol._quic.tls._peer_certificate` instead of a raw DER cert off
a TCP socket.

**Important**: this is *not* hole-punching. It's the same direct-dial (or
rendezvous-resolved-then-direct-dial) model as before, just over UDP instead
of TCP. Switching transport is the *prerequisite* for hole-punching, not
hole-punching itself — see "What this deliberately skips" below.

## What this deliberately skips

Per the design doc, discovery and NAT traversal are separate problems from
trust. A minimal rendezvous/presence service is included (see below) — it
covers "look up a device's current address by ID." STUN + coordinated UDP
hole-punching **were implemented and tested against real, independent NATs**
(`p2p_poc/stun_client.py`, `PunchAwareQuicServer` in `quic_transport.py`,
`POST /api/punch`) — see [`gcp/PUNCH_FINDINGS.md`](gcp/PUNCH_FINDINGS.md) for
the full result: the mechanism is proven correct, but the actual punch is
blocked by Cloud NAT's filtering behavior specifically, a real and evidenced
finding, not an unfinished feature. No TURN relay fallback exists yet — the
documented next step for when punching fails. mDNS (LAN auto-discovery) also
isn't implemented — addresses are still supplied manually via
`--host`/`--port`/`--rendezvous`.

## Requirements

- Python 3.11+
- `pip install -r requirements.txt` (ideally in a venv)

```bash
cd p2p-poc
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Hosting: two devices on one laptop

Run two instances, each with its own port and data directory (separate data
dirs = separate identities, exactly like two physical devices):

```bash
# terminal 1
python -m p2p_poc.main --port 8001 --data-dir ./data/device-a

# terminal 2
python -m p2p_poc.main --port 8002 --data-dir ./data/device-b
```

Each prints its `device_id` and starts serving `https://127.0.0.1:<port>`.

Open both in a browser:

- `https://127.0.0.1:8001`
- `https://127.0.0.1:8002`

Your browser will warn about the self-signed certificate — **this is
expected and correct**: the whole point of this design is that there's no CA
involved, and expected click-through here (`Advanced → Proceed`) is
standing in for the pairing step actually verifying trust, not a browser
CA chain.

**To pair:**

1. On device A's page, click **Generate pairing code** and copy the text.
2. On device B's page, paste it into the **Pair with another device** box
   and click **Pair**.
3. B's page reloads showing A in **Known devices**. Reload A's page — A now
   shows B too (each side records trust independently once its half of the
   exchange succeeds).

**To see the MITM-detection path fire:** copy a generated code, edit the
`device_id` field in a scratch buffer (decode with
`python -c "import base64,sys; print(base64.urlsafe_b64decode(sys.argv[1]))" <code>`,
tweak it, re-encode), and submit the tampered code. Pairing fails with a
`certificate mismatch` message — proving the pinning check runs *before* any
payload is trusted, not just as an afterthought.

## Hosting: two real machines

Nothing above is loopback-specific — the pairing code carries whatever
`host`/`port` you tell each instance to advertise. To pair across a LAN (two
laptops) or a GCP VM instead of two local processes:

```bash
python -m p2p_poc.main --host 0.0.0.0 --port 8001 --data-dir ./data/device-a
```

`--host 0.0.0.0` binds on all interfaces; make sure the pairing code's
advertised address is one the *other* device can actually reach (e.g. the
machine's LAN IP, or a GCP VM's external IP with port 8001 opened in the
firewall). This is a manual stand-in for what the rendezvous service in the
main design doc would normally automate.

## Rendezvous extension: WAN discovery

Everything above assumes the pairing code's embedded `host`/`port` is
actually correct — fine on a LAN or with a routable address, but not what a
real NAT'd home device can offer (it usually doesn't know its own reachable
address at all). `p2p_poc/rendezvous.py` is a minimal presence service that
separates *discovery* from *trust*, matching the split in the design doc:
devices register their current address there, and a peer resolves that
address by `device_id` instead of trusting whatever the pairing code says.
The trust mechanism itself (fingerprint pinning, signature verification) is
completely unchanged — only where the `host`/`port` used to dial comes from.

It's deliberately plain HTTP with no auth: per the design doc, this service
only ever sees device IDs and addresses, never anything sensitive, and it
plays no role in the trust decision.

**The demo that actually proves it's doing something** (not just "it still
works," which would be true even if the lookup were a no-op): pair using a
code whose embedded address is *wrong*, and watch it succeed anyway because
the live lookup wins.

```bash
# terminal 1 — the rendezvous service
python -m p2p_poc.rendezvous --port 9000

# terminal 2 — device A, registers itself with rendezvous at startup
python -m p2p_poc.main --port 8001 --data-dir ./data/device-a --rendezvous http://127.0.0.1:9000

# terminal 3 — device B, also configured with rendezvous
python -m p2p_poc.main --port 8002 --data-dir ./data/device-b --rendezvous http://127.0.0.1:9000
```

Generate a pairing code from A (`https://127.0.0.1:8001`, same UI as before),
then **deliberately corrupt it** before pasting it into B:

```bash
python3 -c "
import base64, json, sys
code = input('paste the code: ')
data = json.loads(base64.urlsafe_b64decode(code))
data['port'] = 1  # a port nothing is listening on
print(base64.urlsafe_b64encode(json.dumps(data).encode()).decode())
"
```

Paste the *tampered* output into device B's pairing box. It still succeeds —
B never dials port `1`; it looks up A's real, currently-registered address
from rendezvous and uses that instead. Stop device A with `--rendezvous`
omitted (or stop the rendezvous service) and repeat: now the same tampered
code fails, because there's nothing left to correct the bad address with.
That contrast is the whole point — see
`tests/test_rendezvous_integration.py` for the same proof as an automated
test (including the control case).

## Real public-IP deployment

The above is all loopback. `rendezvous.py` is also deployed for real on Cloud
Run (backed by Firestore, not the in-memory store — see
`rendezvous_store.py`), and was paired end-to-end against a device running on
a real GCP VM with an external IP, from this laptop sitting behind ordinary
home NAT with zero port forwarding. See [`gcp/README.md`](gcp/README.md) for
the live addresses, how to test it yourself, and `gcp/teardown.sh` to tear
the VM down when done (the serverless rendezvous piece is cheap enough to
leave running).

## Testing

```bash
cd p2p-poc
pytest
```

Three layers, matching Tier 1 of the testing plan in the design doc:

- `tests/test_identity.py` — device_id derivation is stable across restarts
  and distinct per data dir.
- `tests/test_pairing.py` — pairing-code encode/decode/expiry, and signature
  verification (including the case where a signature from the wrong key must
  be rejected).
- `tests/test_integration.py` — spins up **two real HTTPS servers** on
  loopback (real TLS handshakes, real self-signed certs, ephemeral ports),
  and drives the actual pairing flow end-to-end — the `/api/pairing/accept`
  call is HTTP, but internally triggers the real QUIC/UDP confirm exchange:
  - `test_full_pairing_flow_trusts_both_directions` — the happy path, both
    devices end up trusting each other.
  - `test_tampered_pairing_code_is_rejected` — the MITM case: a code with a
    `device_id` that doesn't match the cert actually presented is rejected
    before any payload is sent, and no trust is recorded.
  - `test_expired_pairing_code_is_rejected` — expiry is enforced.
- `tests/test_quic_transport.py` — exercises `quic_transport.py` directly,
  bypassing FastAPI/HTTP entirely, the most unambiguous proof the exchange is
  genuinely QUIC:
  - `test_quic_confirm_round_trip` / `test_quic_pin_mismatch_is_rejected` —
    same pinning/signature guarantees as the TCP version had.
  - `test_quic_server_occupies_udp_not_tcp` — binds a *TCP* socket on the
    exact same port number while the QUIC server is running (succeeds — proves
    it's not holding TCP there), then a second *UDP* socket on that port
    (fails — proves it genuinely holds the UDP namespace).

This is exactly the "two processes on one laptop" test strategy discussed for
the main design — no Docker or GCP needed for this tier. Real NAT
traversal/mDNS testing (Tier 2/3 in the design doc) is out of scope for this
POC.

## A real finding from building this: Ed25519 certs and Chrome

The device keypair is **ECDSA P-256**, not Ed25519. It started as Ed25519 (a
cleaner, more modern default), but real-browser testing surfaced a genuine
interoperability gap: Chrome/BoringSSL sends a `signature_algorithms_cert`
extension in its ClientHello that doesn't include `ed25519`, so a server
presenting an Ed25519 certificate hits `NO_SUITABLE_SIGNATURE_ALGORITHM` and
silently closes the connection *before* the TLS handshake even reaches a
certificate-trust decision — which is why it showed up as a bare
`ERR_CONNECTION_CLOSED` with no certificate warning at all. Confirmed by
capturing Chrome's actual ClientHello via `chrome://net-export` and replaying
it directly at the server with Python's `ssl` module. Python's own OpenSSL
binding (and `openssl s_client`) fully support Ed25519 certs, which is why
early testing looked fine — only real browser testing caught it. ECDSA P-256
is universally supported (it's also what Syncthing itself uses for device
certs) and needed no other design changes — same pinning-by-public-key-hash
mechanism, just a different curve.

See [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) for the full diagnostic
playbook (capturing a Chrome netlog, extracting and replaying the real
ClientHello, getting the exact OpenSSL error) — reusable for any future
"works with curl/Python, fails in the browser" TLS mystery.

## Known limitations (POC, not production)

- No revocation UI (only a `KnownDevicesStore.revoke()` method, unused by the
  routes).
- No rate limiting on pairing attempts.
- Pending pairing codes live in an in-memory dict — restarting a device
  invalidates any pairing code it generated but hadn't yet had accepted.
- Self-signed cert browser warnings are expected and not suppressed —
  suppressing them would be the wrong lesson to take from this POC.
- `quic_pinned_confirm()` reads `protocol._quic.tls._peer_certificate` —
  aioquic has no public API for this, so pinning depends on private
  attributes that could change between aioquic versions without warning.
- UDP has no TCP-style "connection refused"; a dead/unreachable peer just
  produces silence. `CONNECT_TIMEOUT_SECONDS` (2s) bounds this, but that's a
  POC-appropriate number for loopback testing, not a tuned value for real
  network conditions.
- Still no hole-punching/STUN/TURN — the transport is UDP now, which is the
  prerequisite, but connectivity is still direct-dial-or-rendezvous-resolved,
  same as the TCP version. See "What this deliberately skips" above.

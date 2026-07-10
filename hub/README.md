# yaffo-hub

The one piece of always-on infrastructure in the P2P sharing design
([`docs/development/p2p-sharing.md`](../docs/development/p2p-sharing.md)):
WebSocket signaling (presence + opaque JSON forwarding), a DERP-style UDP
datagram relay, and STUN on the relay port. One Python process, deliberately
independent of the Yaffo app package — it deploys alone
(see [`deploy/hub/`](../deploy/hub/README.md)).

The hub is **trust-irrelevant by design**: it never sees keys or photo
plaintext and plays no role in any trust decision. Devices authenticate each
other end-to-end (certificate pinning inside their QUIC handshake); the hub
only routes ciphertext.

## Hardening over the POC hub (`p2p-poc/p2p_poc/hub.py`)

- **Authenticated signaling** — on connect the hub sends a challenge nonce;
  the device answers with its pubkey and an ECDSA signature. The hub verifies
  the signature *and* that the device ID in the URL is the hash of that
  pubkey (IDs are self-authenticating — no account registry). Stops device-ID
  squatting and spoofed signaling.
- **Relay sessions tied to signaling** — the relay only accepts `HELLO`s for
  tokens the hub itself just brokered (allowlisted, short TTL, as a
  `connect_request`/`connect_response` pair is forwarded). Random internet
  UDP can't create sessions; unauthorized HELLOs get no ACK.
- **Limits** — per-session idle TTL and byte caps, per-device concurrent
  brokered-session caps, per-IP rate limits on signaling connects, small max
  WebSocket message size.
- **Ops** — `/healthz` (status + connected device count + relay stats,
  including `total_bytes_forwarded`, the cost signal), structured key=value
  logs, config via `YAFFO_HUB_*` env vars or flags (flags win).

## Signaling protocol

```
client → wss://hub.<domain>/ws/<device_id>
hub    → {"type": "challenge", "nonce": "<b64>"}
client → {"type": "auth", "pubkey": "<b64 X962 P-256>", "signature": "<b64 ECDSA-SHA256 over nonce>"}
hub    → {"type": "hub_info", "relay_port": 40000}          # authenticated
```

After `hub_info`, any JSON object with a `"to": "<device_id>"` field is
forwarded verbatim to that device (stamped with `"from"`), or answered with
a `{"type": "error"}` if the target holds no open socket. Close codes:
`4401` auth timeout, `4403` auth failed, `4429` rate limited, `4409`
replaced by a newer authenticated connection.

## Run locally

```bash
python -m venv venv && venv/bin/pip install -e ".[dev]"
venv/bin/python -m yaffo_hub --host 127.0.0.1 --port 8080 --relay-port 40000
venv/bin/python -m pytest            # unit + loopback integration tests
```

Cross-package compatibility (POC devices pairing/pulling through *this* hub)
is covered by `p2p-poc/tests/test_production_hub_integration.py`, run with
the POC suite.

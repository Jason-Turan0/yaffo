# Diagnosing "works with curl/Python, fails in the browser" TLS bugs

A playbook, written up after diagnosing a real bug in this POC: Chrome showed
`ERR_CONNECTION_CLOSED` connecting to the pairing server, while `curl` gave a
different (also broken) error, and a Python `ssl` client connected fine. None
of the three tools agreed, which is itself the important clue — see Step 1.

## Symptom

- Python's `http.client` + `ssl.CERT_NONE` client: connects fine, gets real
  responses (used throughout earlier automated testing).
- System `curl` (macOS, LibreSSL-linked): `SSL_ERROR_SYSCALL` — connection
  dropped mid-handshake.
- Chrome: `ERR_CONNECTION_CLOSED`, no certificate warning shown at all (which
  matters — see Step 2).

**Lesson going in**: three different TLS client implementations gave three
different results against the same server. That pattern — "my lenient test
client is happy, but every strict/real-world client fails" — is a strong
signal the test client isn't exercising something the others require. Don't
trust a client whose verification you've deliberately weakened (`CERT_NONE`)
to tell you the server is healthy.

## Step 1 — Get a second opinion from a strict, standards-compliant client

```bash
openssl s_client -connect 127.0.0.1:8001 -tls1_3
```

This succeeded (`Verify return code: 18 (self-signed certificate)` is just
openssl flagging the expected self-signed state, not a failure — the
handshake itself completed). This ruled out "the server is fundamentally
broken" and pointed at something *specific to Chrome's* handshake — different
TLS clients send meaningfully different ClientHellos (extensions, cipher
lists, signature algorithms), so "openssl works" doesn't mean "every client
works."

## Step 2 — Get Chrome's own account of what happened

Generic browser error pages are useless for this ("connection was closed" —
closed *how*, *when*, *by whom*). Chrome's own network log has the real
detail:

1. `chrome://net-export/` → **Start Logging to Disk** → reproduce the failure
   → **Stop**. Saves a `.json` file.
2. Either load it at `https://netlog-viewer.appspot.com/` (entirely
   client-side, no upload) or parse the JSON directly — it's a documented,
   fairly readable format: a `constants` block maps numeric event/source type
   codes to names, and an `events` array has `{time, source, type, phase,
   params}`.

Parsed directly with a small script (grep for `127.0.0.1:8001` across
`params` to find the relevant `source.id`s, then print that subset sorted by
time). The decisive events:

```
SOCKET_BYTES_SENT      byte_count=1701          <- Chrome's ClientHello
SOCKET_BYTES_RECEIVED  byte_count=0             <- server sent NOTHING back
SSL_HANDSHAKE_ERROR    net_error=ERR_CONNECTION_CLOSED, ssl_error=1
```

**This is the key finding of the whole investigation**: the server closed the
connection *before* sending a `ServerHello` — meaning this had nothing to do
with certificate trust/warnings (which happen *after* a `ServerHello` +
certificate exchange). Whatever was wrong, it was happening during ClientHello
parsing, server-side, before any cert was even presented.

## Step 3 — Extract Chrome's exact ClientHello and replay it

The netlog's `SSL_HANDSHAKE_MESSAGE_SENT` event includes the raw bytes Chrome
sent, base64-encoded, in `params.bytes`. Extracted and decoded:

```python
import json, base64
data = json.load(open("chrome-net-export-log.json"))
for e in data["events"]:
    if e["source"]["id"] == <the relevant socket source id> and e["params"].get("bytes"):
        raw = base64.b64decode(e["params"]["bytes"])
        open("/tmp/chrome_clienthello.bin", "wb").write(raw)
```

**Gotcha**: this field logs the TLS *Handshake protocol message* only
(`HandshakeType(1) + length(3) + body`, starting `0x01 0x00 0x06 0x9c...`),
**not** the full record-layer bytes that actually went over the wire. Feeding
it directly to a raw socket gave a misleading `WRONG_VERSION_NUMBER` error —
that was an artifact of missing the record header, not the real bug. Fixed by
wrapping it in a proper TLS record before replaying:

```python
import struct
record = bytes([0x16, 0x03, 0x01]) + struct.pack(">H", len(handshake_msg)) + handshake_msg
```

## Step 4 — Get the *exact* OpenSSL error, not just "connection closed"

Replaying the properly-framed bytes at a live server over a raw socket still
just showed "0 bytes back, closed" — same as Chrome, a successful
reproduction, but asyncio/uvicorn swallowed the underlying exception without
logging it. To get the actual OpenSSL error message, drove the handshake
manually with `ssl.MemoryBIO` instead of a live socket:

```python
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert_path, key_path)
incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
obj = ctx.wrap_bio(incoming, outgoing, server_side=True)
incoming.write(record)
obj.do_handshake()   # raises with the real error instead of just closing
```

This raised:

```
SSLError: [SSL: NO_SUITABLE_SIGNATURE_ALGORITHM] no suitable signature algorithm
```

— the server's certificate's signature algorithm (Ed25519) wasn't in the
intersection of what it could sign with and what Chrome's ClientHello
advertised as acceptable in `signature_algorithms_cert`. Root cause found.

## Step 5 — Verify the fix against the *original* captured bytes, not a new test

After switching the cert to ECDSA P-256, re-ran **the same replay** (Chrome's
literal captured ClientHello, unmodified) against a freshly generated server.
It now returned a real `ServerHello` (bytes starting `0x16`, the handshake
content type) instead of closing — proof the fix addresses the exact input
that broke in production, not just a new, possibly-different test case.

## Reusable takeaways

- When a lenient test client (`verify=False`/`CERT_NONE`) says a server is
  fine but real clients disagree, distrust the lenient client — it may not be
  exercising the part that's actually broken (here: certificate signature
  algorithm negotiation, which `CERT_NONE` skips entirely).
- `openssl s_client` is a fast way to get a second, standards-compliant
  opinion before assuming a browser-specific bug.
- `chrome://net-export/` + parsing the JSON directly (no need to upload
  anywhere) turns "the browser closed the connection" into a precise,
  byte-level account of what was sent, received, and when.
- Chrome's netlog logs the *handshake message*, not the *wire record* — add
  the 5-byte TLS record header back before replaying captured bytes anywhere.
- `ssl.MemoryBIO` lets you drive a handshake by hand and get the real
  exception message, when a live async server (uvicorn/asyncio) would
  otherwise just silently close the socket on the same error.
- Always re-verify a fix against the *original* captured failing input, not a
  fresh reproduction — it's the strongest evidence the actual bug is gone.

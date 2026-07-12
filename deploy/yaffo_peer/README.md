# yaffo-peer — temporary GCP test instance

A full Yaffo install on a throwaway GCE VM, for testing the p2p sharing
stack (pairing, grants, Phase 6 batch transfers) against your local
instance over the real internet and the production hub at
`wss://hub.yaffo.app`. Same script style as `p2p-poc/gcp/`: plain
idempotent shell, a dedicated passphrase-less SSH key (auto-generated,
gitignored), no Terraform — this is disposable infrastructure, unlike
`deploy/hub/`.

## Scripts

| Script            | What it does                                                                                                                                     |
|-------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| `create.sh`       | Creates the private peer VM (`e2-standard-2`, Debian 13 for Python 3.13) plus a small NAT VM, installs the **current local working tree** as a pip package, runs it under systemd, and seeds `/opt/yaffo/media` with `yaffo_ui_tests/test_data`. Re-running redeploys code + restarts. `SKIP_SEED=1` skips the fixture copy. `--nat-profile=HARD` switches the NAT VM to symmetric-NAT-like behavior; default is `PUNCHABLE`. |
| `copy-media.sh`   | Copies a local directory's contents into the VM's media dir: `./copy-media.sh ~/Pictures/trip`. No argument = the UI-test fixtures.               |
| `mount-data-dir.sh` | Mounts the VM's Yaffo data dir (`/opt/yaffo/data`) locally via `sshfs` only for legacy/direct-IP peers. The private NAT topology uses IAP, so use `gcloud compute ssh` or `copy-media.sh` there. |
| `port-forward.sh` | SSH tunnel to the peer's UI at `http://localhost:5601` (waitress binds 127.0.0.1 on the VM — the tunnel is the only way in). `LOCAL_PORT=…` to change. |
| `teardown.sh`     | Deletes the VM (and with it the media, DB, and p2p identity).                                                                                     |

## Typical session

```bash
cd deploy/yaffo_peer
./create.sh              # ~5-10 min first time (apt + pip + model downloads)
./port-forward.sh        # keep running; open http://localhost:5601
```

To validate relay fallback behind a hard NAT:

```bash
./create.sh --nat-profile=HARD
```

NAT profiles:

- `PUNCHABLE` (default): plain iptables `MASQUERADE`, preserving source
  ports when possible. This models a typical punchable home router.
- `HARD`: `MASQUERADE --random`, giving a fresh random source port per
  flow. UDP punching should fail and Yaffo should stay on the relay.

Then in the browser:

1. **Peer UI** (localhost:5601): Settings → add `/opt/yaffo/media` as a
   media directory; indexing picks up the seeded test files. Set a
   download directory on the Sharing page if you'll pull files *to* the
   peer.
2. Pair: generate a pairing code on either instance's Sharing tab and
   paste it on the other (both must be running; your local instance via
   `inv app-local`).
3. Grant a folder on one side, browse it from the other, and hit
   **Download all** — the transfers panel shows the path each batch used.
   With `PUNCHABLE`, a compatible local NAT should produce `Direct`; with
   `HARD`, expect `Via relay (metered)`.

```bash
./copy-media.sh ~/some/photos   # add more files any time
./mount-data-dir.sh             # inspect /opt/yaffo/data at /tmp/yaffo-peer-data
./mount-data-dir.sh unmount     # detach the sshfs mount
./teardown.sh                   # when done — the VM bills while it exists
```

## Notes / gotchas

- **Device identity survives restarts via a plaintext keyring**
  (`keyrings.alt.file.PlaintextKeyring`): headless Debian has no OS
  keychain, and with no backend at all the identity would be
  ephemeral-per-boot, breaking the pairing on every service restart.
  Plaintext is acceptable for a throwaway test box only.
- **Nothing is exposed to the internet** from the peer VM: it has no
  external IP. SSH, copy, and UI tunnels use IAP; the peer's outbound
  internet path goes through the NAT VM.
- **First startup is slow**: the service downloads the CLIP and
  InsightFace models into the data dir before indexing works. Watch with
  `sudo journalctl -u yaffo-peer -f` on the VM.
- **After teardown, revoke the peer** on your local instance's Sharing
  tab — the identity died with the VM and can never answer again.
- Cost while up: e2-standard-2 ≈ $0.07/hour plus a few cents of disk.

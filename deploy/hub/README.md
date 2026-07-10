# yaffo-hub Deployment (Terraform)

Infrastructure-as-code for the always-on hub VM from
[`docs/development/p2p-sharing.md`](../../docs/development/p2p-sharing.md),
Phase 1. The hub *code* lives in [`hub/`](../../hub/README.md); Terraform owns
the infrastructure, [`deploy.sh`](deploy.sh) owns code delivery.

What gets created:

| Resource | Name | Purpose |
|---|---|---|
| Static external IP | `yaffo-hub-ip` | Survives VM replacement; `hub.<domain>`'s A record points here |
| Firewall rules | `yaffo-hub-ingress`, `yaffo-hub-iap-ssh` | 80+443/tcp (Caddy), one UDP port (relay+STUN); SSH via IAP only |
| VM | `yaffo-hub` | `e2-micro`, Debian 12, 10 GB pd-standard; startup script installs Caddy + the systemd unit |

On the VM: **Caddy** terminates TLS on 443 (auto-obtains/renews the Let's
Encrypt cert for `hub.<domain>`) and reverse-proxies to the hub process on
localhost; **`yaffo-hub.service`** runs the hub under systemd
(`Restart=always`, hardened, logs to journald). The UDP relay is exposed
directly — it forwards already-encrypted QUIC and terminates no TLS.

## Prerequisites

1. **Terraform** (>= 1.5): `brew install hashicorp/tap/terraform`.
2. **gcloud** authenticated for Terraform:
   `gcloud auth application-default login`.
3. **A domain you own.** Clients dial `wss://hub.<domain>`, never the IP —
   that's what makes the VM replaceable. Any registrar works (Cloud Domains
   keeps it on the GCP bill; Cloudflare/Porkbun are at-cost). Only one A
   record is needed, so registrar DNS is fine — no Cloud DNS zone.

## Usage

```bash
cd deploy/hub
cp terraform.tfvars.example terraform.tfvars   # edit project_id + hub_domain

terraform init
terraform plan      # read this carefully
terraform apply     # creates IP + firewall + VM (VM boots, service waits for code)

# Create the A record shown in the output at your registrar (TTL ~1h):
terraform output dns_record

./deploy.sh         # push hub code, restart the service, verify /healthz
```

`deploy.sh` is idempotent — run it for the first deploy and after every hub
code change. It verifies the local `/healthz` and then the public
`https://hub.<domain>/healthz` (which proves DNS + Caddy + certificate +
proxy end to end; on a brand-new A record, ACME may need a few minutes).

## SSH access

Port 22 is closed to the internet (a tag-scoped deny overrides the default
VPC's `default-allow-ssh`); the only way in is an IAP tunnel **with the
dedicated automation key**:

```bash
gcloud compute ssh yaffo-hub --zone=us-central1-a --project=<project_id> --tunnel-through-iap --ssh-key-file=deploy/hub/yaffo-hub-admin-key
```

(`terraform output -raw ssh_command` prints this command with your values
filled in.)

Two things make the `--ssh-key-file` flag mandatory:

- The key it points to, `deploy/hub/yaffo-hub-admin-key`, is a dedicated
  **passphrase-less** automation key (gitignored). Personal keys tend to
  carry passphrases, which fail *silently* in non-interactive shells —
  ssh can't prompt without a TTY and reports a misleading
  `Permission denied (publickey)`. Same lesson as the POC's `gcp/` scripts.
- Its public half is what the startup script installs in the admin user's
  `authorized_keys` (via the `admin_ssh_user`/`admin_ssh_pubkey` variables
  in `terraform.tfvars`), independent of GCE's metadata-based key mechanism.

Copying files works the same way:

```bash
gcloud compute scp somefile yaffo-hub:/tmp/ --zone=us-central1-a --project=<project_id> --tunnel-through-iap --ssh-key-file=deploy/hub/yaffo-hub-admin-key
```

**If the key is lost** (new laptop, deleted checkout): generate a fresh one
and roll it out — the VM is stateless, so the replacement is a non-event:

```bash
ssh-keygen -t ed25519 -N "" -f deploy/hub/yaffo-hub-admin-key -C yaffo-hub-automation
# put the new .pub contents in terraform.tfvars as admin_ssh_pubkey, then:
terraform apply    # replaces the VM (static IP + DNS survive)
./deploy.sh        # re-push the hub code
```

## Operations

```bash
terraform output -raw ssh_command     # SSH via IAP (see "SSH access" above)
sudo journalctl -u yaffo-hub -f       # hub logs, once on the VM (structured key=value events)
curl -s https://hub.<domain>/healthz  # status + relay stats
```

`/healthz` includes `relay.total_bytes_forwarded` — the cost signal: relay
egress is the only meaningful variable cost, paid only by transfers that
failed to hole-punch. Limits are tunable without a redeploy via
`YAFFO_HUB_*` vars in `/etc/default/yaffo-hub` (then
`sudo systemctl restart yaffo-hub`).

**Costs** (see the design doc's table): ~$0–7/mo for the VM ($0 if the
billing account's always-free `e2-micro` slot is unclaimed), ~$0.40 disk,
relay egress typically under $2, plus the domain (~$12–20/yr).

**Replacing the VM** is a non-event for installs in the field: the static IP
and DNS record survive `terraform destroy`/`apply` of the instance
(`yaffo-hub-ip` has `prevent_destroy` so it can't be dropped accidentally);
re-run `./deploy.sh` afterwards. The hub keeps no state worth backing up —
presence and relay sessions are ephemeral by design.

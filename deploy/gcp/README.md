# GCP public-demo infrastructure

This Terraform stack creates the Phase 2 infrastructure for the two-instance
Yaffo demo. Application delivery is handled separately by
[`deploy/demo/deploy.sh`](../demo/deploy.sh), so infrastructure and image rollout
remain independently reviewable.

It provisions:

- one dedicated VPC/subnet with only HTTPS/ACME and IAP SSH ingress;
- explicit runtime egress for DNS, NTP, HTTPS, metadata HTTP, and the operated
  hub relay UDP port, followed by a deny-all backstop;
- one reserved IPv4 address for the three exact public hostnames;
- one Shielded `e2-medium` Container-Optimized OS VM;
- one protected persistent disk containing isolated A/B data and identity trees;
- a least-privilege service account that can pull images and emit logs/metrics;
- an Artifact Registry repository with digest-based deployment and bounded retention;
- a daily 7:45 AM–10:00 PM `America/Chicago` instance schedule; and
- a USD 50 monthly budget with 50%, 80%, 100%, and forecast notifications.

## Prerequisites

1. Terraform 1.5 or newer and the Google Cloud CLI.
2. Application Default Credentials:

   ```bash
   gcloud auth application-default login
   ```

3. Project and billing-account permissions for Compute Engine, Artifact
   Registry, project IAM, Monitoring channels, and Billing Budgets.
4. The SHA-256 published for the pinned Linux x86-64 Docker Compose release.

## Apply

```bash
cd deploy/gcp
cp terraform.tfvars.example terraform.tfvars
# Fill in the project, billing account, operator email, and Compose checksum.
terraform init
terraform plan
terraform apply
```

Create the three A records printed by `terraform output dns_a_records`. No
wildcard or catch-all DNS record is used.

The VM uses OS Login and has no public SSH rule. Connect with the command from
`terraform output -raw iap_ssh_command`.

## Deploy the containers

Build and push Yaffo for `linux/amd64`, then deploy immutable image references:

```bash
./deploy/demo/build-and-push.sh v0.1.0-demo.1
./deploy/demo/deploy.sh \
  'us-central1-docker.pkg.dev/PROJECT/yaffo-demo/yaffo-demo@sha256:…' \
  'caddy:2.10.2-alpine@sha256:…'
```

The deploy command rejects tag-only references. Caddy publishes 80/443; the A/B
web and P2P ports remain private to Docker. The Compose plugin installed by the
startup script is version- and checksum-pinned and is used only as an operator
tool—it is not mounted into any application container.

## Teardown

`terraform destroy` intentionally stops at the static address and data-disk
`prevent_destroy` guards. Remove those guards only during an explicit, reviewed
decommission. Terraform state and real `terraform.tfvars` remain gitignored.

# GCP Demo Deployment (Terraform)

Infrastructure-as-code to deploy Yaffo as a single-container app on a Compute
Engine VM. Design and rationale:
[`docs/deployment/gcp-demo-architecture.md`](../../docs/deployment/gcp-demo-architecture.md).

This is a Terraform configuration. `terraform apply` provisions the environment;
`terraform destroy` decommissions it.

## Files

| File | Purpose |
|---|---|
| `versions.tf` | Terraform + Google provider version pins and provider config |
| `variables.tf` | Input variables (project, region, disk/VM sizing) |
| `main.tf` | The resources: APIs, Artifact Registry repo, data disk, COS VM |
| `outputs.tf` | Useful values printed after apply (image repo path, IAP command) |
| `terraform.tfvars.example` | Template for your values — copy to `terraform.tfvars` |

## Prerequisites

1. **Terraform** (>= 1.5). Install: `brew install terraform` (macOS) or see
   https://developer.hashicorp.com/terraform/install.
2. **gcloud CLI** authenticated for Terraform to use:
   ```bash
   gcloud auth application-default login
   ```
   (This is separate from `gcloud auth login`; Terraform uses the
   "application default credentials" it creates.)
3. A GCP project with **billing enabled**.

## Usage

```bash
cd deploy/gcp
cp terraform.tfvars.example terraform.tfvars   # edit project_id etc.

terraform init      # download the Google provider (first time only)
terraform plan      # preview what will be created — read this carefully
terraform apply     # create the resources (type 'yes' to confirm)
```

After `apply`, see the outputs (or run `terraform output`) for the image repo
path and the IAP tunnel command.

The VM is created with **no external IP** on purpose: personal access is via an
IAP tunnel (a later step), and the demo step adds the public IP + firewall only
when you want it exposed.

## Teardown

```bash
terraform destroy
```

Note: the data disk has `prevent_destroy = true` in `main.tf` as a safety guard,
so `destroy` will error rather than delete your photos/DBs. To intentionally tear
the disk down, set that to `false` in `main.tf`, `apply`, then `destroy`.

## State & secrets

- `terraform.tfstate` (records real resource ids) and `terraform.tfvars` (your
  project id) are **gitignored**. The provider lock file
  (`.terraform.lock.hcl`) and `terraform.tfvars.example` are committed.
- For a solo demo, local state is fine. For team use you'd move state to a GCS
  backend — out of scope here.

## Cost note

The VM and persistent disk bill while they exist. Stop the VM when idle
(`gcloud compute instances stop <vm_name> --zone <zone>`); the disk still bills
but compute does not. `terraform destroy` removes the VM and repo entirely.

# Pins Terraform itself and the Google provider to known-good versions.
# `terraform init` reads this, downloads the provider, and records exact
# versions in .terraform.lock.hcl (commit that lock file).

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0" # any 6.x, but not 7.x
    }
  }

  # State lives in GCS instead of the local .gitignored tfstate file so it
  # survives a lost/wiped laptop and gets GCS object versioning as a backup
  # history. Bucket is created out-of-band (not by this config — a backend
  # can't bootstrap the bucket it depends on) with versioning enabled.
  backend "gcs" {
    bucket = "gen-lang-client-0392476874-tfstate"
    prefix = "demo"
  }
}

# The provider block configures *how* Terraform talks to GCP.
# Auth comes from your local gcloud credentials:
#   gcloud auth application-default login
#
# user_project_override + billing_project: the provider does NOT read the
# quota_project_id from your ADC file on its own (that's a gcloud-CLI/client-
# library convention, not something hashicorp/google honors by default).
# Without this, every request goes out with no quota project attached, and
# some APIs — billingbudgets.googleapis.com among them — reject that with a
# SERVICE_DISABLED error attributed to gcloud's own bootstrap OAuth client
# project (764086051850), not yours, no matter what `gcloud auth
# application-default set-quota-project` reports.
provider "google" {
  project               = var.project_id
  region                = var.region
  zone                  = var.zone
  user_project_override = true
  billing_project       = var.project_id
}

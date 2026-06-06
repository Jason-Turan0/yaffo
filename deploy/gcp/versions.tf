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
}

# The provider block configures *how* Terraform talks to GCP.
# Auth comes from your local gcloud credentials:
#   gcloud auth application-default login
provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
# Resources for the Yaffo demo environment.
# See docs/deployment/gcp-demo-architecture.md for the overall design.
#
# `terraform apply`   creates/updates everything here.
# `terraform destroy` decommissions it (replaces the old 99_teardown.sh).
# Terraform figures out create/update/delete by diffing this config against
# its recorded state — you describe the desired end state, not the steps.

# --- Required APIs ------------------------------------------------------------
# for_each over a set creates one resource instance per API. Without this the
# other resources would fail until the APIs were enabled.
# disable_on_destroy = false: leave APIs enabled on `destroy` (they're free and
# may be used by other things in the project).
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "iap.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- Artifact Registry (Docker image repo) -----------------------------------
resource "google_artifact_registry_repository" "yaffo" {
  location      = var.region
  repository_id = var.ar_repo
  format        = "DOCKER"
  description   = "Yaffo container images"

  # depends_on makes the API-enable complete before we try to use the API.
  depends_on = [google_project_service.apis]
}

# --- Persistent data disk (photos + SQLite DBs, mounted at /data) -------------
resource "google_compute_disk" "data" {
  name = var.disk_name
  type = var.disk_type
  zone = var.zone
  size = var.disk_size_gb

  # Guard against `terraform destroy` silently nuking the data disk.
  # Flip to false (and re-apply) when you actually intend to tear it down.
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

# --- Container-Optimized OS VM with the data disk attached --------------------
# No external IP (network_interface has no access_config block): personal access
# is via IAP tunnel. The demo step adds a static IP + firewall separately.
resource "google_compute_instance" "yaffo" {
  name         = var.vm_name
  machine_type = var.machine_type
  zone         = var.zone
  tags         = [var.vm_tag]

  boot_disk {
    initialize_params {
      image = "cos-cloud/cos-stable"
    }
  }

  # Attach the persistent data disk. device_name controls the /dev/disk/by-id
  # path the startup/entrypoint uses to mount it at /data.
  attached_disk {
    source      = google_compute_disk.data.id
    device_name = var.disk_name
    mode        = "READ_WRITE"
  }

  network_interface {
    network = "default"
    # No access_config => no public IP.
  }

  # cloud-platform scope lets the VM pull from Artifact Registry, etc.
  service_account {
    scopes = ["cloud-platform"]
  }

  depends_on = [google_project_service.apis]
}
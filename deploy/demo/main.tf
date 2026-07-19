# Infrastructure for the public two-instance demo VM. See
# docs/development/demo-environment.md for the product/security design; this
# file only provisions infrastructure. `deploy.sh` pushes the application
# bundle separately (like deploy/hub's split) — Terraform never rebuilds or
# restarts the app on every apply, only on a real infra change.
#
# Modeled on deploy/hub, the pattern that has actually been run in
# production: the default VPC with an explicit tag-scoped SSH deny (instead
# of a dedicated VPC), a static admin SSH key installed by the startup
# script (instead of relying on OS Login, which proved unreliable), and an
# idempotent startup script that finishes the job — including starting the
# containers, which the previous version of this config never did.

# --- Required APIs ------------------------------------------------------------
resource "google_project_service" "apis" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "compute.googleapis.com",
    "iap.googleapis.com", # SSH via IAP tunnel — port 22 is not open to the internet
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- Image registry -------------------------------------------------------------
resource "google_artifact_registry_repository" "yaffo" {
  location      = var.region
  repository_id = var.ar_repo
  format        = "DOCKER"
  description   = "Digest-addressed Yaffo public-demo runtime images"

  docker_config {
    # Deployments consume digests, while allowing cleanup to remove old uniquely
    # tagged builds after the keep policy no longer selects them.
    immutable_tags = false
  }

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"

    most_recent_versions {
      keep_count = var.image_versions_to_keep
    }
  }

  cleanup_policies {
    id     = "delete-older-versions"
    action = "DELETE"

    condition {
      tag_state = "ANY"
    }
  }

  depends_on = [google_project_service.apis]
}

# --- Runtime service account ----------------------------------------------------
# Least-privilege: pulls the pinned image and emits logs/metrics. No API scope
# the containers themselves need beyond that.
resource "google_service_account" "runtime" {
  account_id   = var.service_account_id
  display_name = "Yaffo demo runtime"
  description  = "Pulls the pinned demo image and emits application logs/metrics."

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "runtime" {
  for_each = toset([
    "roles/artifactregistry.reader",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# --- Static external IP ---------------------------------------------------------
# All three hostnames (walkthrough, device A, device B) point here. Reserved
# separately from the VM so replacing the instance never requires a DNS change.
resource "google_compute_address" "demo" {
  name   = var.address_name
  region = var.region

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

# --- Firewall -------------------------------------------------------------------
# Default VPC, same as deploy/hub: exactly the surface the demo needs
# (80/443 for Caddy, IAP-only SSH), plus an explicit tag-scoped deny so this
# VM doesn't inherit the default VPC's broad default-allow-ssh rule. No
# custom egress rules — the previous version's egress allow/deny set was
# never applied against real traffic and added meaningful complexity for
# unproven benefit; revisit only if observed abuse demonstrates a need.
resource "google_compute_firewall" "demo_ingress" {
  name    = "${var.vm_tag}-ingress"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.vm_tag]

  depends_on = [google_project_service.apis]
}

resource "google_compute_firewall" "demo_iap_ssh" {
  name    = "${var.vm_tag}-iap-ssh"
  network = "default"
  # Beat demo_deny_ssh (1000) so IAP gets through.
  priority = 900

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  # IAP's published TCP-forwarding range — `gcloud compute ssh --tunnel-through-iap`.
  source_ranges = ["35.235.240.0/20"]
  target_tags   = [var.vm_tag]

  depends_on = [google_project_service.apis]
}

resource "google_compute_firewall" "demo_deny_ssh" {
  name     = "${var.vm_tag}-deny-ssh"
  network  = "default"
  priority = 1000

  deny {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.vm_tag]

  depends_on = [google_project_service.apis]
}

# --- Persistent data disk --------------------------------------------------------
resource "google_compute_disk" "data" {
  name = var.disk_name
  type = var.disk_type
  zone = var.zone
  size = var.disk_size_gb

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

# --- Daily start/stop schedule ---------------------------------------------------
# The public demo's operating hours (docs/development/demo-environment.md,
# Phase 0). Startup brings the containers up; see files/startup.sh.tftpl.
resource "google_compute_resource_policy" "schedule" {
  name   = "${var.vm_name}-daily-schedule"
  region = var.region

  instance_schedule_policy {
    time_zone = var.schedule_timezone

    vm_start_schedule {
      schedule = var.vm_start_cron
    }

    vm_stop_schedule {
      schedule = var.vm_stop_cron
    }
  }

  depends_on = [google_project_service.apis]
}

# --- The demo VM ------------------------------------------------------------------
resource "google_compute_instance" "demo" {
  name                      = var.vm_name
  machine_type              = var.machine_type
  zone                      = var.zone
  tags                      = [var.vm_tag]
  allow_stopping_for_update = true
  resource_policies         = [google_compute_resource_policy.schedule.self_link]

  boot_disk {
    initialize_params {
      image = "cos-cloud/cos-stable"
      size  = var.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.data.id
    device_name = var.disk_name
    mode        = "READ_WRITE"
  }

  network_interface {
    network = "default"

    access_config {
      nat_ip = google_compute_address.demo.address
    }
  }

  service_account {
    email  = google_service_account.runtime.email
    scopes = ["cloud-platform"]
  }

  # block-project-ssh-keys: don't inherit any project-wide SSH keys, only the
  # instance-specific one below. ssh-keys is GCE's standard instance-metadata
  # mechanism (the guest agent provisions the user/home/authorized_keys on
  # first connect) — this is COS's actual supported path, not deploy/hub's
  # Debian-only static-user-provisioning approach. Empty admin_ssh_pubkey
  # skips provisioning (merge with {} below).
  metadata = merge(
    {
      block-project-ssh-keys = "TRUE"
      serial-port-enable     = "FALSE"
    },
    var.admin_ssh_user != "" && var.admin_ssh_pubkey != "" ? {
      ssh-keys = "${var.admin_ssh_user}:${var.admin_ssh_pubkey}"
    } : {}
  )

  metadata_startup_script = templatefile("${path.module}/files/startup.sh.tftpl", {
    disk_name                          = var.disk_name
    docker_compose_version             = var.docker_compose_version
    docker_compose_linux_x86_64_sha256 = var.docker_compose_linux_x86_64_sha256
    restore_golden_script = templatefile("${path.module}/files/restore-golden.sh.tftpl", {
      registry_host = "${var.region}-docker.pkg.dev"
    })
  })

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    provisioning_model  = "STANDARD"
  }

  depends_on = [
    google_project_iam_member.runtime,
    google_project_service.apis,
  ]
}

# --- Budget --------------------------------------------------------------------
resource "google_monitoring_notification_channel" "budget_email" {
  display_name = "Yaffo demo budget owner"
  type         = "email"
  labels = {
    email_address = var.budget_alert_email
  }

  depends_on = [google_project_service.apis]
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_billing_budget" "demo" {
  billing_account = var.billing_account_id
  display_name    = "Yaffo public demo"

  budget_filter {
    projects        = ["projects/${data.google_project.current.number}"]
    calendar_period = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.8
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.budget_email.name]
    disable_default_iam_recipients   = false
    enable_project_level_recipients  = true
  }

  depends_on = [google_project_service.apis]
}

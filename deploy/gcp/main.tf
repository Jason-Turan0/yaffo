locals {
  required_apis = toset([
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "compute.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])

  runtime_roles = toset([
    "roles/artifactregistry.reader",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_service" "apis" {
  for_each = local.required_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

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
resource "google_service_account" "runtime" {
  account_id   = var.service_account_id
  display_name = "Yaffo demo runtime"
  description  = "Pulls the pinned demo image and emits application logs/metrics."

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "runtime" {
  for_each = local.runtime_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_compute_network" "demo" {
  name                    = var.network_name
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  depends_on = [google_project_service.apis]
}

resource "google_compute_subnetwork" "demo" {
  name                     = "${var.network_name}-${var.region}"
  region                   = var.region
  network                  = google_compute_network.demo.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

resource "google_compute_address" "demo" {
  name   = var.address_name
  region = var.region

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

resource "google_compute_firewall" "public_https" {
  name      = "${var.network_name}-public-https"
  network   = google_compute_network.demo.id
  direction = "INGRESS"

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges           = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.runtime.email]
}

resource "google_compute_firewall" "iap_ssh" {
  name      = "${var.network_name}-iap-ssh"
  network   = google_compute_network.demo.id
  direction = "INGRESS"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges           = ["35.235.240.0/20"]
  target_service_accounts = [google_service_account.runtime.email]
}

resource "google_compute_firewall" "egress_https" {
  name      = "${var.network_name}-egress-https"
  network   = google_compute_network.demo.id
  direction = "EGRESS"
  priority  = 900

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.runtime.email]
}

resource "google_compute_firewall" "egress_infrastructure" {
  name      = "${var.network_name}-egress-infrastructure"
  network   = google_compute_network.demo.id
  direction = "EGRESS"
  priority  = 900

  allow {
    protocol = "tcp"
    ports    = ["53"]
  }

  allow {
    protocol = "udp"
    ports    = ["53", "123", tostring(var.hub_relay_udp_port)]
  }

  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.runtime.email]
}

resource "google_compute_firewall" "egress_metadata" {
  name      = "${var.network_name}-egress-metadata"
  network   = google_compute_network.demo.id
  direction = "EGRESS"
  priority  = 800

  allow {
    protocol = "tcp"
    ports    = ["80"]
  }

  destination_ranges      = ["169.254.169.254/32"]
  target_service_accounts = [google_service_account.runtime.email]
}

resource "google_compute_firewall" "egress_deny" {
  name      = "${var.network_name}-egress-deny"
  network   = google_compute_network.demo.id
  direction = "EGRESS"
  priority  = 65534

  deny {
    protocol = "all"
  }

  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.runtime.email]
}

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
    subnetwork = google_compute_subnetwork.demo.id

    access_config {
      nat_ip = google_compute_address.demo.address
    }
  }

  service_account {
    email  = google_service_account.runtime.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
    serial-port-enable     = "FALSE"
  }

  metadata_startup_script = templatefile("${path.module}/files/startup.sh.tftpl", {
    disk_name                          = var.disk_name
    docker_compose_version             = var.docker_compose_version
    docker_compose_linux_x86_64_sha256 = var.docker_compose_linux_x86_64_sha256
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

resource "google_monitoring_notification_channel" "budget_email" {
  display_name = "Yaffo demo budget owner"
  type         = "email"
  labels = {
    email_address = var.budget_alert_email
  }

  depends_on = [google_project_service.apis]
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

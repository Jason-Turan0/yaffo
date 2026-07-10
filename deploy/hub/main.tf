# Infrastructure for yaffo-hub — the one always-on box in the P2P design:
# WebSocket signaling (behind Caddy TLS on 443) + UDP datagram relay + STUN.
# See docs/development/p2p-sharing.md, "Phase 1 — Hub, production-ready".
#
# `terraform apply` provisions the VM and networking; the hub *code* is
# pushed separately with ./deploy.sh (like the app's image push — Terraform
# owns infrastructure, not code delivery). The startup script bakes in
# everything code-independent: Caddy, the systemd unit, the service user.
#
# The VM is replaceable by design: clients dial wss://<hub_domain>, the
# static IP survives VM deletion, and deploy.sh re-pushes code — so
# recreating the instance is a non-event for installs in the field.

# --- Required APIs ------------------------------------------------------------
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "iap.googleapis.com", # SSH via IAP tunnel — port 22 is not open to the internet
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- Static external IP ---------------------------------------------------------
# Reserved separately from the VM so the VM can be deleted/recreated without
# the IP changing — hub.<domain>'s A record points here, and that hostname
# (never the IP) is what ships in clients as the default hub URL.
resource "google_compute_address" "yaffo_hub" {
  name   = var.address_name
  region = var.region

  # Losing this address means a DNS change and propagation wait for every
  # install in the field. Flip to false (and re-apply) only when you mean it.
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

# --- Firewall -------------------------------------------------------------------
# Exactly the doc's surface: 443/tcp (Caddy -> signaling) + one UDP port
# (relay + STUN), plus 80/tcp for Caddy's ACME HTTP-01 challenges and
# HTTP->HTTPS redirect. SSH is IAP-only.
resource "google_compute_firewall" "yaffo_hub_ingress" {
  name    = "yaffo-hub-ingress"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  allow {
    protocol = "udp"
    ports    = [tostring(var.relay_port)]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.vm_tag]

  depends_on = [google_project_service.apis]
}

resource "google_compute_firewall" "yaffo_hub_iap_ssh" {
  name    = "yaffo-hub-iap-ssh"
  network = "default"
  # Beat yaffo-hub-deny-ssh (1000) so IAP gets through.
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

# The default VPC ships a default-allow-ssh rule that opens 22 to the whole
# internet (sshd's journal showed scanners within minutes). Deleting that
# rule would break other VMs in this shared project (the POC's redeploy flow
# SSHes over external IPs), so instead deny 22 specifically for the hub —
# the IAP allow above wins on priority.
resource "google_compute_firewall" "yaffo_hub_deny_ssh" {
  name     = "yaffo-hub-deny-ssh"
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

# --- The hub VM -----------------------------------------------------------------
resource "google_compute_instance" "yaffo_hub" {
  name         = var.vm_name
  machine_type = var.machine_type
  zone         = var.zone
  tags         = [var.vm_tag]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.boot_disk_size_gb
      type  = "pd-standard"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.yaffo_hub.address
    }
  }

  # The default compute service account. The hub itself calls no Google
  # APIs, but the guest agent needs credentials for its machinery (SSH key
  # lookup via google_authorized_keys breaks without one) — same setup as
  # deploy/gcp's VM.
  service_account {
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = templatefile("${path.module}/files/startup.sh.tftpl", {
    admin_ssh_user   = var.admin_ssh_user
    admin_ssh_pubkey = var.admin_ssh_pubkey
    caddyfile = templatefile("${path.module}/files/Caddyfile.tftpl", {
      hub_domain = var.hub_domain
      hub_port   = var.hub_port
    })
    unit_file = templatefile("${path.module}/files/yaffo-hub.service.tftpl", {
      hub_port   = var.hub_port
      relay_port = var.relay_port
    })
  })

  # Config changes (ports, domain) should rebuild the box rather than drift:
  # the startup script only runs at boot.
  allow_stopping_for_update = true

  depends_on = [google_project_service.apis]
}

# Outputs print after `apply` and are queryable with `terraform output`.
# deploy.sh, build-and-push.sh, and emergency-stop.sh read these so this
# config stays the single source of truth for deployment coordinates.

output "project_id" {
  description = "GCP project that owns the demo."
  value       = var.project_id
}

output "image_repo" {
  description = "Base path for immutable application images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.ar_repo}"
}

output "vm_name" {
  description = "Demo VM name."
  value       = google_compute_instance.demo.name
}

output "vm_zone" {
  description = "Demo VM zone."
  value       = google_compute_instance.demo.zone
}

output "public_ipv4" {
  description = "Reserved IPv4 address for all three exact demo hostnames."
  value       = google_compute_address.demo.address
}

output "dns_a_records" {
  description = "A records to create at the authoritative DNS provider."
  value = {
    (var.walkthrough_domain) = google_compute_address.demo.address
    (var.demo_a_domain)      = google_compute_address.demo.address
    (var.demo_b_domain)      = google_compute_address.demo.address
  }
}

output "walkthrough_domain" {
  description = "Static walkthrough hostname."
  value       = var.walkthrough_domain
}

output "demo_a_domain" {
  description = "Device A hostname."
  value       = var.demo_a_domain
}

output "demo_b_domain" {
  description = "Device B hostname."
  value       = var.demo_b_domain
}

output "hub_url" {
  description = "P2P hub URL configured in the demo containers."
  value       = var.hub_url
}

output "public_firewall_rule" {
  description = "Firewall rule disabled by the emergency withdrawal command."
  value       = google_compute_firewall.demo_ingress.name
}

output "deploy_command" {
  description = "Deploy a pinned application and Caddy image after Terraform apply."
  value       = "deploy/demo/deploy.sh <yaffo-image@sha256:digest> <caddy-image@sha256:digest>"
}

output "demo_urls" {
  description = "Public HTTPS URLs after DNS propagation and deployment."
  value = {
    walkthrough = "https://${var.walkthrough_domain}"
    device_a    = "https://${var.demo_a_domain}"
    device_b    = "https://${var.demo_b_domain}"
  }
}

output "ssh_command" {
  description = "SSH to the VM via IAP (port 22 is not open to the internet). The --ssh-key-file flag is required — see README 'SSH access'."
  value       = "gcloud compute ssh ${google_compute_instance.demo.name} --zone=${google_compute_instance.demo.zone} --project=${var.project_id} --tunnel-through-iap --ssh-key-file=${path.module}/yaffo-demo-admin-key"
}

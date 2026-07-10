# Outputs print after `apply` and are queryable with `terraform output`.

# These three are read by deploy.sh so the Terraform config stays the single
# source of truth for deployment coordinates.
output "project_id" {
  description = "GCP project the hub lives in."
  value       = var.project_id
}

output "vm_name" {
  description = "Name of the hub VM."
  value       = google_compute_instance.yaffo_hub.name
}

output "vm_zone" {
  description = "Zone of the hub VM."
  value       = google_compute_instance.yaffo_hub.zone
}

output "hub_domain" {
  description = "Hostname the hub serves TLS for."
  value       = var.hub_domain
}

output "hub_port" {
  description = "Localhost port the hub process listens on (behind Caddy)."
  value       = var.hub_port
}

output "static_ip" {
  description = "The reserved static external IP the hub serves from."
  value       = google_compute_address.yaffo_hub.address
}

output "dns_record" {
  description = "The A record to create at your registrar (TTL ~1h)."
  value       = "${var.hub_domain}. A ${google_compute_address.yaffo_hub.address}"
}

output "hub_url" {
  description = "The signaling URL clients get as their default."
  value       = "wss://${var.hub_domain}/ws/<device_id>"
}

output "deploy_command" {
  description = "Push the hub code to the VM (run after apply, and after any code change)."
  value       = "./deploy.sh"
}

output "ssh_command" {
  description = "SSH to the VM via IAP (port 22 is not open to the internet). The --ssh-key-file flag is required — see README 'SSH access'."
  value       = "gcloud compute ssh ${google_compute_instance.yaffo_hub.name} --zone=${google_compute_instance.yaffo_hub.zone} --project=${var.project_id} --tunnel-through-iap --ssh-key-file=${path.module}/yaffo-hub-admin-key"
}

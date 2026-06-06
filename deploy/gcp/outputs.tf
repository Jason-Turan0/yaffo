# Outputs print after `apply` and are queryable with `terraform output`.
# Handy for the next deployment steps (build/push image, IAP tunnel).

output "image_repo" {
  description = "Base path for pushing images to Artifact Registry."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.ar_repo}"
}

output "vm_name" {
  description = "Name of the COS VM."
  value       = google_compute_instance.yaffo.name
}

output "vm_zone" {
  description = "Zone of the COS VM."
  value       = google_compute_instance.yaffo.zone
}

output "iap_tunnel_command" {
  description = "Reach the app locally via IAP (no public IP needed)."
  value       = "gcloud compute start-iap-tunnel ${google_compute_instance.yaffo.name} 8080 --local-host-port=localhost:8080 --zone=${google_compute_instance.yaffo.zone}"
}
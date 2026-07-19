# Input variables. Values come from terraform.tfvars (gitignored) or -var flags.
# Each variable can have a type, default, and description. No default => required.

variable "project_id" {
  type        = string
  description = "GCP project id to deploy into (billing must be enabled)."
}

variable "billing_account_id" {
  type        = string
  description = "Billing account id used for the demo's monthly budget."
}

variable "budget_alert_email" {
  type        = string
  description = "Operator email that receives demo budget notifications."
}

variable "region" {
  type        = string
  description = "Region for the address, repository, and schedule."
  default     = "us-central1"
}

variable "zone" {
  type        = string
  description = "Zone for the demo VM and persistent disk."
  default     = "us-central1-a"
}

variable "walkthrough_domain" {
  type        = string
  description = "Public hostname for the static walkthrough."
  default     = "demo.yaffo.app"
}

variable "demo_a_domain" {
  type        = string
  description = "Public hostname for Device A."
  default     = "demo-a.yaffo.app"
}

variable "demo_b_domain" {
  type        = string
  description = "Public hostname for Device B."
  default     = "demo-b.yaffo.app"
}

variable "hub_url" {
  type        = string
  description = "Authenticated WebSocket URL used by both demo devices."
  default     = "wss://hub.yaffo.app"
}

# --- Naming: "yaffo-demo" everywhere a name appears --------------------------

variable "vm_name" {
  type        = string
  description = "Demo VM name."
  default     = "yaffo-demo"
}

variable "address_name" {
  type        = string
  description = "Name of the reserved static external IP (all three hostnames point here)."
  default     = "yaffo-demo-ip"
}

variable "vm_tag" {
  type        = string
  description = "Network tag on the VM, used to target firewall rules."
  default     = "yaffo-demo"
}

variable "service_account_id" {
  type        = string
  description = "Least-privilege runtime service-account id (pulls images, writes logs/metrics)."
  default     = "yaffo-demo-runtime"
}

variable "ar_repo" {
  type        = string
  description = "Artifact Registry Docker repository name."
  default     = "yaffo-demo"
}

variable "image_versions_to_keep" {
  type        = number
  description = "Most recent application image versions retained in Artifact Registry."
  default     = 3

  validation {
    condition     = var.image_versions_to_keep >= 1 && var.image_versions_to_keep <= 10
    error_message = "image_versions_to_keep must be between 1 and 10."
  }
}

# --- Admin SSH access ---------------------------------------------------------
# The startup script installs this key directly in the admin user's
# authorized_keys, the same belt-and-braces approach as deploy/hub: GCE's
# metadata-based SSH / OS Login has proven unreliable in practice, and a VM
# you can't SSH into can't be operated. Public key material only — safe in
# tfvars.

variable "admin_ssh_user" {
  type        = string
  description = "Login name for the admin user the startup script provisions."
  default     = ""
}

variable "admin_ssh_pubkey" {
  type        = string
  description = "OpenSSH public key line for the admin user. Empty = skip provisioning."
  default     = ""
}

# --- Sizing --------------------------------------------------------------------

variable "machine_type" {
  type        = string
  description = "Demo VM machine type."
  default     = "e2-medium"
}

variable "boot_disk_size_gb" {
  type        = number
  description = "Container-Optimized OS boot disk size in GiB."
  default     = 20
}

variable "disk_name" {
  type        = string
  description = "Persistent disk that stores both isolated demo data trees."
  default     = "yaffo-demo-data"
}

variable "disk_size_gb" {
  type        = number
  description = "Size of the persistent demo disk in GiB."
  default     = 50
}

variable "disk_type" {
  type        = string
  description = "Persistent disk type."
  default     = "pd-balanced"
}

# --- Schedule --------------------------------------------------------------------

variable "schedule_timezone" {
  type        = string
  description = "IANA timezone used by the VM start/stop policy."
  default     = "America/Chicago"
}

variable "vm_start_cron" {
  type        = string
  description = "Daily VM startup cron; startup brings the containers up before serving."
  default     = "45 7 * * *"
}

variable "vm_stop_cron" {
  type        = string
  description = "Daily VM stop cron."
  default     = "0 22 * * *"
}

# --- Budget --------------------------------------------------------------------

variable "monthly_budget_usd" {
  type        = number
  description = "Monthly demo budget in whole USD."
  default     = 50

  validation {
    condition     = var.monthly_budget_usd >= 1 && floor(var.monthly_budget_usd) == var.monthly_budget_usd
    error_message = "monthly_budget_usd must be a positive whole number."
  }
}

# --- Docker Compose on Container-Optimized OS ----------------------------------
# COS ships Docker but no general-purpose package manager, so the pinned
# Compose CLI plugin is fetched and checksummed at boot instead of apt-installed.

variable "docker_compose_version" {
  type        = string
  description = "Pinned Docker Compose release installed as an operator-only CLI plugin."
  default     = "v5.1.4"
}

variable "docker_compose_linux_x86_64_sha256" {
  type        = string
  description = "Published SHA-256 for the pinned docker-compose-linux-x86_64 binary."

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.docker_compose_linux_x86_64_sha256))
    error_message = "docker_compose_linux_x86_64_sha256 must be 64 lowercase hexadecimal characters."
  }
}

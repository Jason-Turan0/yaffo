# Input variables. Values come from terraform.tfvars (gitignored) or -var flags.
# Each variable can have a type, default, and description. No default => required.

variable "project_id" {
  type        = string
  description = "GCP project id to deploy into (billing must be enabled)."
}

variable "region" {
  type        = string
  description = "Region for regional resources (the static IP)."
  default     = "us-central1"
}

variable "zone" {
  type        = string
  description = "Zone for the hub VM."
  default     = "us-central1-a"
}

variable "hub_domain" {
  type        = string
  description = <<-EOT
    Fully qualified hostname the hub serves TLS for, e.g. "hub.yaffo.app".
    Prerequisite: you own the domain and will point an A record at the
    static IP this config reserves (see the `dns_record` output after
    apply). Caddy obtains/renews the Let's Encrypt cert for this name.
  EOT
}

# --- Naming: "yaffo-hub" everywhere a name appears (see p2p-sharing.md) ------

variable "vm_name" {
  type        = string
  description = "Name of the hub VM."
  default     = "yaffo-hub"
}

variable "address_name" {
  type        = string
  description = "Name of the reserved static external IP."
  default     = "yaffo-hub-ip"
}

variable "vm_tag" {
  type        = string
  description = "Network tag on the VM, used to target firewall rules."
  default     = "yaffo-hub"
}

# --- Admin SSH access -----------------------------------------------------------
# The startup script installs this key directly in the admin user's
# authorized_keys. Belt-and-braces alongside GCE's metadata-based SSH: the
# guest agent's dynamic key lookup (google_authorized_keys) has proven
# unreliable on this image/agent combination, and a hub you can't SSH into
# can't be deployed to. Public key material only — safe in tfvars.

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

# --- Sizing / ports -----------------------------------------------------------

variable "machine_type" {
  type        = string
  description = "VM machine type. e2-micro is free-tier eligible in some US regions if the billing account's slot is unclaimed."
  default     = "e2-micro"
}

variable "boot_disk_size_gb" {
  type        = number
  description = "Boot disk size in GB."
  default     = 10
}

variable "relay_port" {
  type        = number
  description = "UDP port for the datagram relay + STUN."
  default     = 40000
}

variable "hub_port" {
  type        = number
  description = "Localhost TCP port the hub process listens on; Caddy reverse-proxies 443 to it. Not exposed externally."
  default     = 8080
}

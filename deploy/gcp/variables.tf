# Input variables. Values come from terraform.tfvars (gitignored) or -var flags.
# Each variable can have a type, default, and description. No default => required.

variable "project_id" {
  type        = string
  description = "GCP project id to deploy into (billing must be enabled)."
}

variable "region" {
  type        = string
  description = "Region for regional resources (Artifact Registry, static IP)."
  default     = "us-central1"
}

variable "zone" {
  type        = string
  description = "Zone for zonal resources (disk, VM)."
  default     = "us-central1-a"
}

# --- Artifact Registry --------------------------------------------------------

variable "ar_repo" {
  type        = string
  description = "Artifact Registry Docker repository name."
  default     = "yaffo"
}

# --- Persistent data disk (photos + SQLite DBs, mounted at /data) -------------

variable "disk_name" {
  type        = string
  description = "Name of the persistent data disk."
  default     = "yaffo-data"
}

variable "disk_size_gb" {
  type        = number
  description = "Size of the persistent data disk in GB."
  default     = 50
}

variable "disk_type" {
  type        = string
  description = "Disk type (pd-standard, pd-balanced, pd-ssd)."
  default     = "pd-balanced"
}

# --- Compute Engine VM (Container-Optimized OS) ------------------------------

variable "vm_name" {
  type        = string
  description = "Name of the COS VM."
  default     = "yaffo"
}

variable "machine_type" {
  type        = string
  description = "VM machine type."
  default     = "e2-standard-4"
}

variable "vm_tag" {
  type        = string
  description = "Network tag on the VM, used to target firewall rules."
  default     = "yaffo"
}
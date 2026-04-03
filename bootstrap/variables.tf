variable "cluster_name" {
  description = "Cluster Name"
  type        = string
  default     = "abox"
}

variable "github_owner" {
  description = "GitHub owner (user or org) for GHCR image publishing"
  type        = string
  default     = "mkhomytsya"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "course-ai-reliability-engineering-agentic-infrastructure-workshop-lab4"
}

locals {
  oci_registry = "oci://ghcr.io/${var.github_owner}/${var.github_repo}"
}

variable "releases_version" {
  description = "Default tag for releases OCI artifact bootstrap"
  type        = string
  default     = "0.1.0"
}

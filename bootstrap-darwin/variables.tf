variable "cluster_name" {
  description = "Cluster Name"
  type        = string
  default     = "abox"
}

variable "oci_registry" {
  description = "OCI registry base URL"
  type        = string
  default     = "oci://ghcr.io/mkhomytsya/course-ai-reliability-engineering-agentic-infrastructure-workshop-lab2"
}

variable "releases_version" {
  description = "Default tag for releases OCI artifact bootstrap"
  type        = string
  default     = "0.1.0"
}

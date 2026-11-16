variable "region" {
  description = "AWS region. us-east-1 has the lowest prices."
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  type    = string
  default = "ksc-session"
}

variable "kubernetes_version" {
  description = <<-EOT
    Must be in EKS STANDARD support: extended support bills $0.60 per cluster-hour instead of $0.10.
    CI (scripts/check-eks-version.py) fails if this is within 60 days of leaving standard support,
    and the session workflow re-checks it against the live EKS API before creating anything.
  EOT
  type        = string
  default     = "1.36"
}

variable "expires_at" {
  description = "UTC timestamp (RFC 3339) after which the reaper destroys everything tagged project=ksc."
  type        = string
}

variable "runner_cidr" {
  description = "The CI runner's public IP as a /32. The only address allowed to reach the public API endpoint."
  type        = string
  default     = "127.0.0.1/32" # a harmless placeholder for destroy / plan runs

  validation {
    condition     = can(cidrhost(var.runner_cidr, 0)) && endswith(var.runner_cidr, "/32")
    error_message = "runner_cidr must be a single address, like 203.0.113.7/32."
  }
}

variable "system_instance_type" {
  type    = string
  default = "t3.medium"
}

variable "sandbox_instance_type" {
  type    = string
  default = "t3.medium"
}

variable "sandbox_node_count" {
  type    = number
  default = 2

  validation {
    condition     = var.sandbox_node_count >= 1 && var.sandbox_node_count <= 3
    error_message = "A session uses 1 to 3 sandbox nodes (cost ceiling)."
  }
}

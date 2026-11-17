variable "region" {
  type    = string
  default = "us-east-1"
}

variable "owner_email" {
  description = "Receives the budget alerts ($5, $15 and $25 of actual spend, and a forecast alert)."
  type        = string
}

variable "github_repo" {
  description = "owner/name of the repository whose workflows may assume the CI role."
  type        = string
  default     = "manynames3/k8s-sandbox-control-plane"
}

variable "github_sub_prefix" {
  description = <<-EOT
    Prefix of the OIDC `sub` claim GitHub puts in tokens for this repository. Null means the classic "repo:owner/name".
    Repositories that use GitHub's immutable subject claim (owner and repository ids in the subject) need the value
    from: gh api repos/OWNER/NAME/actions/oidc/customization/sub  (the sub_claim_prefix field).
  EOT
  type        = string
  default     = null
}

variable "github_environment" {
  description = "GitHub Environment the session workflow runs in (it requires the owner's approval)."
  type        = string
  default     = "eks-session"
}

variable "create_oidc_provider" {
  description = "Set false if this AWS account already has the GitHub OIDC provider (only one is allowed)."
  type        = bool
  default     = true
}

variable "cluster_name" {
  description = "Must match infra/eks. IAM permissions are scoped to roles and policies named <cluster_name>-*."
  type        = string
  default     = "ksc-session"
}

variable "monthly_budget_usd" {
  type    = number
  default = 25
}

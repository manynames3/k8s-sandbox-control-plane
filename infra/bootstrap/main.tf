provider "aws" {
  region = var.region

  # Deliberately NOT project=ksc: the orphan check fails if anything tagged project=ksc outlives a
  # session, and these resources are meant to persist.
  default_tags {
    tags = {
      project    = "ksc-bootstrap"
      managed-by = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  bucket_name = "ksc-tfstate-${local.account_id}-${var.region}"
  oidc_host   = "token.actions.githubusercontent.com"
  role_prefix = "arn:${data.aws_partition.current.partition}:iam::${local.account_id}:role/${var.cluster_name}"
}

# --- Terraform state: versioned, encrypted, private. Native S3 lockfile locking (no DynamoDB).
resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_145:SSE-S3 (AES256) is used on purpose: a KMS key costs about $1/month, far above the $0.10/month idle target
  #checkov:skip=CKV_AWS_18:access logging would need a second bucket for a single-owner, low-traffic state bucket; CloudTrail data events are the audit path if ever needed
  #checkov:skip=CKV_AWS_144:cross-region replication doubles storage and adds cost; state is versioned and re-creatable from the cluster definition
  #checkov:skip=CKV2_AWS_62:no consumers for event notifications on a state bucket
  bucket = local.bucket_name
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  policy     = data.aws_iam_policy_document.state_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# --- GitHub OIDC: no long-lived AWS keys anywhere.
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://${local.oidc_host}"
}

locals {
  oidc_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

# The role can be assumed only by this repository's session workflow (through the GitHub Environment
# that needs the owner's approval, which should itself be limited to the main branch) and by workflows
# running on main (the reaper). Nothing else, including forks and pull requests, can assume it.
data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values = [
        "repo:${var.github_repo}:environment:${var.github_environment}",
        "repo:${var.github_repo}:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "ci" {
  name                 = "ksc-ci"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 14400 # 4 hours: the longest allowed session
}

# --- What the EKS stack needs, and no more than that. EC2 and EKS are region-limited; IAM is limited to
# --- roles and policies named <cluster_name>-*, to a fixed list of managed policies, and to passing
# --- those roles to EKS and EC2 only. docs/AWS.md explains each statement and the remaining breadth.
data "aws_iam_policy_document" "ci" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/eks/*"] # the state file and its .tflock
  }

  statement {
    sid       = "Ec2AndVpcInRegion"
    actions   = ["ec2:*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }
  statement {
    sid       = "EksInRegion"
    actions   = ["eks:*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }
  statement {
    sid       = "ReadOnlyHelpers"
    actions   = ["autoscaling:Describe*", "tag:GetResources", "sts:GetCallerIdentity", "iam:ListRoles", "iam:ListPolicies"]
    resources = ["*"]
  }
  statement {
    sid       = "EksAmiLookup"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:${data.aws_partition.current.partition}:ssm:${var.region}::parameter/aws/service/eks/*"]
  }

  statement {
    sid = "ManageSessionRoles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole", "iam:UntagRole", "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
    ]
    resources = ["${local.role_prefix}-*"]
  }
  statement {
    sid       = "AttachOnlyTheManagedPoliciesEksNeeds"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = ["${local.role_prefix}-*"]
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values = [
        "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSClusterPolicy",
        "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSVPCResourceController",
        "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSWorkerNodePolicy",
        "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly",
        "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKS_CNI_Policy",
      ]
    }
  }
  statement {
    sid = "ManageSessionPolicies"
    actions = [
      "iam:CreatePolicy", "iam:DeletePolicy", "iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicyVersions",
      "iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:TagPolicy",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:policy/${var.cluster_name}-*"]
  }
  statement {
    sid       = "PassSessionRolesOnlyToEksAndEc2"
    actions   = ["iam:PassRole"]
    resources = ["${local.role_prefix}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["eks.amazonaws.com", "ec2.amazonaws.com"]
    }
  }
  statement {
    sid       = "InstanceProfilesForNodeRoles"
    actions   = ["iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:GetInstanceProfile", "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile", "iam:TagInstanceProfile"]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:instance-profile/${var.cluster_name}-*"]
  }
  statement {
    sid       = "ServiceLinkedRolesEksNeeds"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:role/aws-service-role/*"]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["eks.amazonaws.com", "eks-nodegroup.amazonaws.com", "autoscaling.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "ci" {
  name   = "ksc-ci"
  policy = data.aws_iam_policy_document.ci.json
}

resource "aws_iam_role_policy_attachment" "ci" {
  role       = aws_iam_role.ci.name
  policy_arn = aws_iam_policy.ci.arn
}

# --- Budget alerts: $5, $15 and $25 of actual monthly spend (20%, 60% and 100% of the $25 limit),
# --- plus a forecast alert. Alerts only; nothing is stopped automatically.
resource "aws_budgets_budget" "monthly" {
  name         = "ksc-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [20, 60, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.owner_email]
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.owner_email]
  }
}

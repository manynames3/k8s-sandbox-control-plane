provider "aws" {
  region = var.region

  # Everything created here carries these tags; the reaper and the orphan check key off them.
  default_tags {
    tags = {
      project    = "ksc"
      expires-at = var.expires_at
      managed-by = "terraform"
    }
  }
}

data "aws_availability_zones" "available" {
  #checkov:skip=CKV_AWS_394:the result is sliced to exactly two AZs (local.azs), so a new AZ cannot change what is built; hard-coding AZ letters would break on accounts that lack one
  state = "available"

  # Only regular AZs (no Local Zones / Wavelength), so the result cannot silently grow.
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

# --- Network: public subnets only, in two AZs. No NAT gateway (about $32/month each, plus data),
# --- no load balancer. Nodes get public IPs; their security groups allow no inbound traffic from
# --- the internet. The trade-off is documented in docs/AWS.md.
module "vpc" {
  #checkov:skip=CKV_TF_1:registry modules cannot be pinned by commit; the exact version is pinned and the lock file records the checksum
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.7.3"

  name = var.cluster_name
  cidr = "10.20.0.0/16"
  azs  = local.azs

  public_subnets  = ["10.20.0.0/24", "10.20.1.0/24"]
  private_subnets = []

  enable_nat_gateway      = false
  create_igw              = true
  map_public_ip_on_launch = true
  enable_dns_hostnames    = true
  enable_dns_support      = true
}

# --- Cluster
module "eks" {
  #checkov:skip=CKV_TF_1:registry modules cannot be pinned by commit; the exact version is pinned and the lock file records the checksum
  source  = "terraform-aws-modules/eks/aws"
  version = "21.29.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  # Fixed role names so the CI role's IAM permissions can be scoped to ksc-* (infra/bootstrap).
  iam_role_name            = "${var.cluster_name}-cluster"
  iam_role_use_name_prefix = false

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.public_subnets

  # The public endpoint is reachable only from the runner. Nodes (public subnets) reach the API
  # through the private endpoint, which costs nothing.
  endpoint_public_access       = true
  endpoint_public_access_cidrs = [var.runner_cidr]
  endpoint_private_access      = true

  # Cost guards: no Auto Mode (adds per-instance fees), no control-plane log shipping, no KMS key,
  # no IRSA provider. None of them is needed for a short-lived proof session.
  compute_config              = { enabled = false }
  enabled_log_types           = []
  create_cloudwatch_log_group = false
  create_kms_key              = false
  encryption_config           = null
  enable_irsa                 = false

  enable_cluster_creator_admin_permissions = true

  addons = {
    coredns    = { most_recent = true }
    kube-proxy = { most_recent = true }
    vpc-cni = {
      most_recent    = true
      before_compute = true
      # NetworkPolicy enforcement on EKS comes from the VPC CNI (not Calico).
      configuration_values = jsonencode({ enableNetworkPolicy = "true" })
    }
  }

  eks_managed_node_groups = {
    # Trusted components: API, dispatcher, KEDA.
    system = {
      instance_types = [var.system_instance_type]
      ami_type       = "AL2023_x86_64_STANDARD"
      min_size       = 1
      max_size       = 1
      desired_size   = 1
      labels         = { "ksc.dev/pool" = "system" }

      iam_role_name            = "${var.cluster_name}-system-node"
      iam_role_use_name_prefix = false

      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required" # IMDSv2 only
        http_put_response_hop_limit = 1
      }
    }

    # Untrusted code only: tainted, so nothing else schedules here.
    sandbox = {
      instance_types = [var.sandbox_instance_type]
      ami_type       = "AL2023_x86_64_STANDARD"
      min_size       = var.sandbox_node_count
      max_size       = var.sandbox_node_count
      desired_size   = var.sandbox_node_count
      labels         = { "ksc.dev/pool" = "sandbox" }

      iam_role_name            = "${var.cluster_name}-sandbox-node"
      iam_role_use_name_prefix = false

      taints = {
        sandbox = { key = "ksc.dev/sandbox", value = "true", effect = "NO_SCHEDULE" }
      }

      # Fork-bomb containment, as on kind: cap the processes per pod at 256 through the AL2023 node config.
      # tests/attack asserts this (the fork count must stop below 256 with EAGAIN).
      cloudinit_pre_nodeadm = [{
        content_type = "application/node.eks.aws"
        content      = <<-EOT
          ---
          apiVersion: node.eks.aws/v1alpha1
          kind: NodeConfig
          spec:
            kubelet:
              config:
                podPidsLimit: 256
        EOT
      }]

      # IMDSv2 required with a hop limit of 1: a pod's extra network hop means it cannot reach the
      # instance metadata service, so it cannot read the node's credentials.
      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 1
      }
    }
  }
}

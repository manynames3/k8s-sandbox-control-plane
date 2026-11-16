terraform {
  required_version = ">= 1.10.0" # native S3 lockfile locking

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }

  # Configured at init time by the session workflow:
  #   terraform init -backend-config="bucket=..." -backend-config="key=eks/terraform.tfstate" \
  #                  -backend-config="region=..." -backend-config="use_lockfile=true"
  backend "s3" {}
}

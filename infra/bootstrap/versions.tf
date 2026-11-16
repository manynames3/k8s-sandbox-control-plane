terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }
  # Local state on purpose: this stack creates the bucket that holds every other state. Applied once
  # by the owner from their own machine; the state file is git-ignored (*.tfstate*).
}

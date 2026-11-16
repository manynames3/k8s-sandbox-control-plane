output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "ci_role_arn" {
  description = "Add this to GitHub as the repository variable AWS_ROLE_ARN."
  value       = aws_iam_role.ci.arn
}

output "region" {
  value = var.region
}

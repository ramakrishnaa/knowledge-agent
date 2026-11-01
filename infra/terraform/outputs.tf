output "app_url" {
  description = "Open this in a browser."
  value       = "https://${aws_cloudfront_distribution.app.domain_name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.bucket
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.app.id
}

output "backend_instance_id" {
  description = "Open a shell with: aws ssm start-session --target <id>"
  value       = aws_instance.backend.id
}

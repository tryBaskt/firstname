output "job_post_sites_table_name" {
  description = "Development job-source ingestion-state table name."
  value       = aws_dynamodb_table.job_post_sites.name
}

output "job_post_sites_table_arn" {
  description = "Development job-source table ARN for future permissions."
  value       = aws_dynamodb_table.job_post_sites.arn
}

output "job_postings_table_name" {
  description = "Development job-postings table name."
  value       = aws_dynamodb_table.job_postings.name
}

output "job_postings_table_arn" {
  description = "Development job-postings table ARN for future permissions."
  value       = aws_dynamodb_table.job_postings.arn
}

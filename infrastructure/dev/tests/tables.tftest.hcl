mock_provider "aws" {}

variables {
  aws_region = "us-east-1"
}

run "dev_tables" {
  command = plan

  assert {
    condition = (
      aws_dynamodb_table.job_post_sites.name == "firstname-dev-job-post-sites" &&
      aws_dynamodb_table.job_postings.name == "firstname-dev-job-postings"
    )
    error_message = "Only dev table names are allowed."
  }

  assert {
    condition = (
      aws_dynamodb_table.job_post_sites.hash_key == "site_id" &&
      aws_dynamodb_table.job_post_sites.range_key == null &&
      aws_dynamodb_table.job_postings.hash_key == "site_id" &&
      aws_dynamodb_table.job_postings.range_key == "source_job_id"
    )
    error_message = "Site and job keys must match the ingestion model."
  }

  assert {
    condition = (
      aws_dynamodb_table.job_post_sites.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.job_postings.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.job_post_sites.deletion_protection_enabled &&
      aws_dynamodb_table.job_postings.deletion_protection_enabled
    )
    error_message = "Both tables must use on-demand capacity and deletion protection."
  }

  assert {
    condition = (
      !aws_dynamodb_table.job_post_sites.point_in_time_recovery[0].enabled &&
      !aws_dynamodb_table.job_postings.point_in_time_recovery[0].enabled
    )
    error_message = "Dev tables should not enable point-in-time recovery."
  }
}

resource "aws_dynamodb_table" "job_post_sites" {
  name                        = "firstname-dev-job-post-sites"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "site_id"
  deletion_protection_enabled = true

  attribute {
    name = "site_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "job_postings" {
  name                        = "firstname-dev-job-postings"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "site_id"
  range_key                   = "source_job_id"
  deletion_protection_enabled = true

  attribute {
    name = "site_id"
    type = "S"
  }

  attribute {
    name = "source_job_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  lifecycle {
    prevent_destroy = true
  }
}

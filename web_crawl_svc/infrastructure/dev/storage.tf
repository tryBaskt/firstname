resource "aws_dynamodb_table" "job_posts" {
  name                        = "${local.prefix}-job-posts"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "job_post_id"
  deletion_protection_enabled = true
  attribute {
    name = "job_post_id"
    type = "S"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_dynamodb_table" "job_boards" {
  name                        = "${local.prefix}-job-boards"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "job_board_id"
  deletion_protection_enabled = true
  attribute {
    name = "job_board_id"
    type = "S"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_dynamodb_table" "job_post_job_boards" {
  name                        = "${local.prefix}-job-post-job-boards"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "job_board_id"
  range_key                   = "job_post_id"
  deletion_protection_enabled = true
  attribute {
    name = "job_board_id"
    type = "S"
  }
  attribute {
    name = "job_post_id"
    type = "S"
  }
  global_secondary_index {
    name            = "job_post_id_index"
    hash_key        = "job_post_id"
    range_key       = "job_board_id"
    projection_type = "ALL"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_dynamodb_table" "sites" {
  name                        = "${local.prefix}-crawl-sites"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "site_id"
  deletion_protection_enabled = true
  attribute {
    name = "site_id"
    type = "S"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_dynamodb_table" "runs" {
  name                        = "${local.prefix}-crawl-runs"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "site_id"
  range_key                   = "crawl_run_id"
  deletion_protection_enabled = true
  attribute {
    name = "site_id"
    type = "S"
  }
  attribute {
    name = "crawl_run_id"
    type = "S"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_dynamodb_table" "pages" {
  name                        = "${local.prefix}-crawl-pages"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "crawl_run_id"
  range_key                   = "canonical_url_hash"
  deletion_protection_enabled = true
  attribute {
    name = "crawl_run_id"
    type = "S"
  }
  attribute {
    name = "canonical_url_hash"
    type = "S"
  }
  attribute {
    name = "site_url_key"
    type = "S"
  }
  attribute {
    name = "crawled_at"
    type = "S"
  }
  global_secondary_index {
    name            = "site_url_crawled_at_index"
    hash_key        = "site_url_key"
    range_key       = "crawled_at"
    projection_type = "ALL"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${local.prefix}-crawl-artifacts-${data.aws_caller_identity.current.account_id}-us-east-1"
  force_destroy = false
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

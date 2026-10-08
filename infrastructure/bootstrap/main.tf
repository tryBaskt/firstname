provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["499133675835"]

  default_tags {
    tags = {
      Project     = "FirstName"
      Environment = "dev"
      ManagedBy   = "Terraform"
    }
  }
}

locals {
  state_bucket = "firstname-dev-tfstate-499133675835-us-east-1"
  table_arns = [
    "arn:aws:dynamodb:us-east-1:499133675835:table/firstname-dev-job-post-sites",
    "arn:aws:dynamodb:us-east-1:499133675835:table/firstname-dev-job-postings",
  ]
}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_s3_bucket" "terraform_state" {
  bucket        = local.state_bucket
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket                  = aws_s3_bucket.terraform_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.terraform_state.arn, "${aws_s3_bucket.terraform_state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_iam_role" "github_dev_deploy" {
  name = "firstname-dev-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:tryBaskt@287338800/firstname@1409745128:environment:dev"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_dev_deploy" {
  name = "firstname-dev-terraform"
  role = aws_iam_role.github_dev_deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ManageDevTableConfiguration"
        Effect   = "Allow"
        Resource = local.table_arns
        Action = [
          "dynamodb:CreateTable", "dynamodb:UpdateTable", "dynamodb:DescribeTable",
          "dynamodb:DescribeContinuousBackups", "dynamodb:UpdateContinuousBackups",
          "dynamodb:DescribeTimeToLive", "dynamodb:UpdateTimeToLive",
          "dynamodb:ListTagsOfResource", "dynamodb:TagResource", "dynamodb:UntagResource",
          "dynamodb:DescribeContributorInsights", "dynamodb:DescribeKinesisStreamingDestination",
          "dynamodb:DescribeTableReplicaAutoScaling", "dynamodb:GetResourcePolicy",
        ]
      },
      {
        Sid      = "ListStateBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = aws_s3_bucket.terraform_state.arn
      },
      {
        Sid      = "ReadWriteDevState"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.terraform_state.arn}/dev/terraform.tfstate"
      },
      {
        Sid      = "ManageDevStateLock"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.terraform_state.arn}/dev/terraform.tfstate.tflock"
      },
    ]
  })
}

output "state_bucket_name" {
  description = "Private versioned Terraform state bucket."
  value       = aws_s3_bucket.terraform_state.id
}

output "dev_deploy_role_arn" {
  description = "GitHub dev environment deployment role."
  value       = aws_iam_role.github_dev_deploy.arn
}

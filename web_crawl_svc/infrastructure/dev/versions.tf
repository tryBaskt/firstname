terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  backend "s3" {
    bucket       = "firstname-dev-tfstate-499133675835-us-east-1"
    key          = "web-crawler/dev/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["499133675835"]
  default_tags {
    tags = {
      Project     = "firstname"
      Environment = "dev"
      Service     = "web-crawler"
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  prefix = "firstname-dev"
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = ["499133675835"]

  default_tags {
    tags = {
      Project     = "FirstName"
      Environment = "dev"
      ManagedBy   = "Terraform"
    }
  }
}

terraform {
  backend "s3" {
    bucket              = "firstname-dev-tfstate-499133675835-us-east-1"
    key                 = "bootstrap/terraform.tfstate"
    region              = "us-east-1"
    encrypt             = true
    use_lockfile        = true
    allowed_account_ids = ["499133675835"]
  }
}

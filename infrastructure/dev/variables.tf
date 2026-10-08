variable "aws_region" {
  description = "AWS region for dev tables."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "Development infrastructure is deployed in us-east-1."
  }
}

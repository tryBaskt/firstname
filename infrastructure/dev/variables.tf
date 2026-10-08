variable "aws_region" {
  description = "AWS region for dev tables. Must be selected explicitly."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+$", var.aws_region))
    error_message = "Provide an AWS region such as us-east-1."
  }
}

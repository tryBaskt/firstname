variable "deploy_workers" {
  description = "Enable Lambda deployment after image digests and reviewed IAM policies are supplied."
  type        = bool
  default     = false
}

variable "worker_image_uri" {
  description = "Container image in this stack's worker ECR repository, pinned by sha256 digest."
  type        = string
  default     = ""
}

variable "schedules_enabled" {
  description = "Explicitly enable refresh and recovery only when ready to crawl."
  type        = bool
  default     = false
}

locals {
  worker_names = toset(var.deploy_workers ? ["web-crawler", "crawl-refresh"] : [])
  worker_definitions = {
    web-crawler = {
      handler     = "web_crawl_svc.handler.lambda_handler"
      memory      = 2048
      timeout     = 120
      environment = local.crawler_environment
    }
    crawl-refresh = {
      handler     = "web_crawl_svc.refresh_handler.lambda_handler"
      memory      = 512
      timeout     = 120
      environment = local.refresh_environment
    }
  }
}

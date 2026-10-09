locals {
  schedules = {
    refresh = {
      expression = "cron(0 2 * * ? *)"
      timezone   = "America/New_York"
      worker     = "crawl-refresh"
      payload    = jsonencode({ action = "nightly_refresh", scheduled_time = "<aws.scheduler.scheduled-time>" })
    }
    recovery = {
      expression = "rate(5 minutes)"
      timezone   = "UTC"
      worker     = "web-crawler"
      payload    = jsonencode({ action = "recover_crawls" })
    }
  }
}

resource "aws_scheduler_schedule_group" "crawler" {
  count = var.deploy_workers ? 1 : 0
  name  = "${local.prefix}-crawler-schedules"
}

resource "aws_iam_role" "scheduler" {
  count                = var.deploy_workers ? 1 : 0
  name                 = "${local.prefix}-crawler-scheduler"
  permissions_boundary = "arn:aws:iam::499133675835:policy/firstname-dev-crawler-runtime-boundary"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnEquals    = { "aws:SourceArn" = aws_scheduler_schedule_group.crawler[0].arn }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  count = var.deploy_workers ? 1 : 0
  name  = "${local.prefix}-crawler-schedule-invoke"
  role  = aws_iam_role.scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = [for worker in aws_lambda_function.workers : worker.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "crawler" {
  for_each                     = var.deploy_workers ? local.schedules : {}
  name                         = "${local.prefix}-crawl-${each.key}"
  group_name                   = aws_scheduler_schedule_group.crawler[0].name
  state                        = var.schedules_enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = each.value.expression
  schedule_expression_timezone = each.value.timezone
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.workers[each.value.worker].arn
    role_arn = aws_iam_role.scheduler[0].arn
    input    = each.value.payload
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
  }
  depends_on = [aws_iam_role_policy.scheduler]
}

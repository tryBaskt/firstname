resource "aws_ecr_repository" "workers" {
  name                 = "${local.prefix}-crawler-workers"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_cloudwatch_log_group" "workers" {
  for_each          = toset(["web-crawler", "crawl-refresh"])
  name              = "/aws/lambda/${local.prefix}-${each.key}"
  retention_in_days = 14
}

resource "aws_ecr_repository_policy" "lambda_pull" {
  repository = aws_ecr_repository.workers.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "LambdaImageRetrieval"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
      Condition = {
        ArnLike = {
          "aws:SourceArn" = [
            "arn:aws:lambda:us-east-1:${data.aws_caller_identity.current.account_id}:function:${local.prefix}-web-crawler",
            "arn:aws:lambda:us-east-1:${data.aws_caller_identity.current.account_id}:function:${local.prefix}-crawl-refresh"
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role" "workers" {
  for_each             = local.worker_names
  name                 = "${local.prefix}-${each.key}-execution"
  permissions_boundary = "arn:aws:iam::499133675835:policy/firstname-dev-crawler-runtime-boundary"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "runtime" {
  for_each = local.worker_names
  name     = "${local.prefix}-${each.key}-runtime"
  role     = aws_iam_role.workers[each.key].id
  policy   = jsonencode(local.runtime_policies[each.key])
}

resource "aws_iam_role_policy" "logs" {
  for_each = local.worker_names
  name     = "${local.prefix}-${each.key}-logs"
  role     = aws_iam_role.workers[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.workers[each.key].arn}:*"
    }]
  })
}

resource "aws_lambda_function" "workers" {
  for_each                       = local.worker_names
  function_name                  = "${local.prefix}-${each.key}"
  role                           = aws_iam_role.workers[each.key].arn
  package_type                   = "Image"
  image_uri                      = var.worker_image_uri
  architectures                  = ["x86_64"]
  memory_size                    = local.worker_definitions[each.key].memory
  timeout                        = local.worker_definitions[each.key].timeout
  reserved_concurrent_executions = each.key == "web-crawler" ? 6 : 1
  image_config {
    command = [local.worker_definitions[each.key].handler]
  }
  environment {
    variables = local.worker_definitions[each.key].environment
  }
  lifecycle {
    precondition {
      condition     = startswith(var.worker_image_uri, "${aws_ecr_repository.workers.repository_url}@sha256:")
      error_message = "Supply a digest-pinned image from the dedicated crawler ECR repository."
    }
  }
  depends_on = [aws_iam_role_policy.runtime, aws_iam_role_policy.logs, aws_ecr_repository_policy.lambda_pull]
}

resource "aws_lambda_event_source_mapping" "crawl" {
  count                   = var.deploy_workers ? 1 : 0
  event_source_arn        = aws_sqs_queue.crawl.arn
  function_name           = aws_lambda_function.workers["web-crawler"].arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = 3 }
}

resource "aws_lambda_event_source_mapping" "crawl_dlq" {
  count                   = var.deploy_workers ? 1 : 0
  event_source_arn        = aws_sqs_queue.crawl_dlq.arn
  function_name           = aws_lambda_function.workers["web-crawler"].arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = 2 }
}

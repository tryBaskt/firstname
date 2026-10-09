resource "aws_sqs_queue" "crawl_dlq" {
  name                       = "${local.prefix}-crawl-dlq"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 720
  sqs_managed_sse_enabled    = true
}

resource "aws_sqs_queue" "crawl" {
  name                       = "${local.prefix}-crawl-queue"
  message_retention_seconds  = 345600
  visibility_timeout_seconds = 720
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.crawl_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "crawl_dlq" {
  queue_url = aws_sqs_queue.crawl_dlq.url
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.crawl.arn]
  })
}

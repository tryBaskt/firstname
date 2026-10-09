# All application environment variables and crawler tuning live here.
# AWS_REGION is supplied by Lambda and must not be set in its environment map.
locals {
  crawler_settings = {
    CRAWLER_MAX_DEPTH               = "4"
    CRAWLER_MAX_ATTEMPTS            = "2"
    CRAWLER_RETRY_DELAY_SECONDS     = "30"
    CRAWLER_LEASE_SECONDS           = "240"
    CRAWLER_REQUEST_TIMEOUT_SECONDS = "10"
    CRAWLER_MAX_RESPONSE_BYTES      = "5242880"
    CRAWLER_USER_AGENT              = "firstname-crawler/1.0 (+https://github.com/tryBaskt/firstname)"
    PARSER_VERSION                  = "v2"
  }

  shared_environment = {
    SITES_TABLE       = aws_dynamodb_table.sites.name
    CRAWL_RUNS_TABLE  = aws_dynamodb_table.runs.name
    CRAWL_PAGES_TABLE = aws_dynamodb_table.pages.name
    CRAWL_QUEUE_URL   = aws_sqs_queue.crawl.url
  }

  crawler_environment = merge(local.shared_environment, local.crawler_settings, {
    APPLICATION_S3_BUCKET     = aws_s3_bucket.artifacts.id
    JOB_POSTS_TABLE           = aws_dynamodb_table.job_posts.name
    JOB_BOARDS_TABLE          = aws_dynamodb_table.job_boards.name
    JOB_POST_JOB_BOARDS_TABLE = aws_dynamodb_table.job_post_job_boards.name
    CRAWL_DLQ_ARN             = aws_sqs_queue.crawl_dlq.arn
    CRAWL_DLQ_URL             = aws_sqs_queue.crawl_dlq.url
  })

  refresh_environment = local.shared_environment
}

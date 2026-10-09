# Action baselines were produced by IAM Policy Autopilot 0.3.0. Limit them to
# deployed resources and omit encryption/object-lambda features we do not use.
locals {
  generated_policies = {
    web-crawler   = jsondecode(file("${path.module}/iam/generated/crawler-baseline.json")).Policies[0].Policy
    crawl-refresh = jsondecode(file("${path.module}/iam/generated/refresh-baseline.json")).Policies[0].Policy
  }
  runtime_resources = {
    "dynamodb" = concat([
      aws_dynamodb_table.sites.arn, aws_dynamodb_table.runs.arn, aws_dynamodb_table.pages.arn,
      aws_dynamodb_table.job_posts.arn, aws_dynamodb_table.job_boards.arn,
      aws_dynamodb_table.job_post_job_boards.arn
    ], ["${aws_dynamodb_table.pages.arn}/index/site_url_crawled_at_index"])
    "s3" = ["${aws_s3_bucket.artifacts.arn}/raw/*", "${aws_s3_bucket.artifacts.arn}/parsed/*",
    "${aws_s3_bucket.artifacts.arn}/verification/*"]
    "sqs" = [aws_sqs_queue.crawl.arn, aws_sqs_queue.crawl_dlq.arn]
  }
  runtime_policies = {
    for name, baseline in local.generated_policies : name => {
      Version = "2012-10-17"
      Statement = concat([
        for service, resources in local.runtime_resources : {
          Effect = "Allow"
          Action = distinct(flatten([
            for statement in baseline.Statement : [
              for action in statement.Action : action
              if startswith(action, "${service}:") && !contains([
                "dynamodb:DeleteItem", "dynamodb:ReadDataForReplication", "dynamodb:WriteDataForReplication",
                "s3:PutObjectAcl", "s3:PutObjectLegalHold", "s3:PutObjectRetention", "s3:PutObjectTagging",
                "s3:GetObjectLegalHold", "s3:GetObjectRetention", "s3:GetObjectTagging", "s3:GetObjectVersion"
              ], action)
            ]
          ]))
          Resource = name == "crawl-refresh" && service == "dynamodb" ? [
            aws_dynamodb_table.sites.arn, aws_dynamodb_table.runs.arn, aws_dynamodb_table.pages.arn
          ] : name == "crawl-refresh" && service == "sqs" ? [aws_sqs_queue.crawl.arn] : resources
        } if service != "s3" || name == "web-crawler"
        ], name == "web-crawler" ? [{
          Effect   = "Allow"
          Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
          Resource = [aws_sqs_queue.crawl.arn, aws_sqs_queue.crawl_dlq.arn]
          }, {
          Effect   = "Allow"
          Action   = ["s3:ListBucket"]
          Resource = [aws_s3_bucket.artifacts.arn]
      }] : [])
    }
  }
}

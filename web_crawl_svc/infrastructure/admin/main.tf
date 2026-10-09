terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    bucket       = "firstname-dev-tfstate-499133675835-us-east-1"
    key          = "web-crawler/admin/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["499133675835"]
  default_tags {
    tags = { Project = "firstname", Environment = "dev", ManagedBy = "terraform" }
  }
}

locals {
  prefix  = "firstname-dev"
  account = "499133675835"
  tables = [for name in ["crawl-sites", "crawl-runs", "crawl-pages", "job-posts", "job-boards", "job-post-job-boards"] :
  "arn:aws:dynamodb:us-east-1:${local.account}:table/${local.prefix}-${name}"]
  artifacts = "arn:aws:s3:::${local.prefix}-crawl-artifacts-${local.account}-us-east-1"
  state     = "arn:aws:s3:::${local.prefix}-tfstate-${local.account}-us-east-1"
  queues    = [for name in ["crawl-queue", "crawl-dlq"] : "arn:aws:sqs:us-east-1:${local.account}:${local.prefix}-${name}"]
  functions = [for name in ["web-crawler", "crawl-refresh"] : "arn:aws:lambda:us-east-1:${local.account}:function:${local.prefix}-${name}"]
  roles = [for name in ["web-crawler-execution", "crawl-refresh-execution", "crawler-scheduler"] :
  "arn:aws:iam::${local.account}:role/${local.prefix}-${name}"]
  baseline          = jsondecode(file("../dev/iam/generated/crawler-baseline.json")).Policies[0].Policy
  generated_actions = distinct(flatten([for statement in local.baseline.Statement : statement.Action]))
}

# CI may delegate runtime access only within this boundary, and cannot modify it.
resource "aws_iam_policy" "runtime_boundary" {
  name = "${local.prefix}-crawler-runtime-boundary"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = [for action in local.generated_actions : action if startswith(action, "dynamodb:")],
      Resource = concat(local.tables, ["${local.tables[2]}/index/*"]) },
      { Effect = "Allow", Action = [for action in local.generated_actions : action if startswith(action, "s3:")],
      Resource = ["${local.artifacts}/*"] },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [local.artifacts] },
      { Effect = "Allow", Action = concat([for action in local.generated_actions : action if startswith(action, "sqs:")],
      ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]), Resource = local.queues },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
      Resource = "arn:aws:logs:us-east-1:${local.account}:log-group:/aws/lambda/${local.prefix}-*:log-stream:*" },
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = local.functions }
    ]
  })
}

resource "aws_iam_role_policy" "crawler_deploy" {
  name = "${local.prefix}-crawler-deploy"
  role = "${local.prefix}-github-deploy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "CrawlerTables", Effect = "Allow", Action = ["dynamodb:*"], Resource = concat(local.tables, [for table in local.tables : "${table}/index/*"]) },
      { Sid = "Artifacts", Effect = "Allow", Action = ["s3:*"], Resource = [local.artifacts, "${local.artifacts}/*"] },
      { Sid = "StateBucket", Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = local.state },
      { Sid = "CrawlerState", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${local.state}/web-crawler/dev/terraform.tfstate" },
      { Sid = "CrawlerStateLock", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${local.state}/web-crawler/dev/terraform.tfstate.tflock" },
      { Sid = "Queues", Effect = "Allow", Action = ["sqs:*"], Resource = local.queues },
      { Sid = "Images", Effect = "Allow", Action = ["ecr:*"], Resource = "arn:aws:ecr:us-east-1:${local.account}:repository/${local.prefix}-crawler-workers" },
      { Sid = "RegistryAuthentication", Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Sid = "Workers", Effect = "Allow", Action = ["lambda:*"], Resource = concat(local.functions, [for function in local.functions : "${function}:*"]) },
      { Sid = "CreateMappings", Effect = "Allow", Action = ["lambda:CreateEventSourceMapping"], Resource = "*",
      Condition = { ArnEquals = { "lambda:FunctionArn" = local.functions } } },
      { Sid = "ListMappings", Effect = "Allow", Action = ["lambda:ListEventSourceMappings"], Resource = "*" },
      { Sid = "MappingOperations", Effect = "Allow", Action = ["lambda:GetEventSourceMapping", "lambda:UpdateEventSourceMapping", "lambda:DeleteEventSourceMapping"], Resource = "arn:aws:lambda:us-east-1:${local.account}:event-source-mapping:*",
      Condition = { ArnEquals = { "lambda:FunctionArn" = local.functions } } },
      { Sid = "MappingTags", Effect = "Allow", Action = ["lambda:ListTags", "lambda:TagResource", "lambda:UntagResource"], Resource = "arn:aws:lambda:us-east-1:${local.account}:event-source-mapping:*",
      Condition = { StringEquals = { "aws:ResourceTag/Project" = "firstname", "aws:ResourceTag/Environment" = "dev" } } },
      { Sid = "Logs", Effect = "Allow", Action = ["logs:*"], Resource = "arn:aws:logs:us-east-1:${local.account}:log-group:/aws/lambda/${local.prefix}-*:*" },
      { Sid = "CreateBoundedRoles", Effect = "Allow", Action = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"], Resource = local.roles,
      Condition = { StringEquals = { "iam:PermissionsBoundary" = aws_iam_policy.runtime_boundary.arn } } },
      { Sid = "ManageExecutionRoles", Effect = "Allow", Action = ["iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:DeleteRole", "iam:TagRole", "iam:UntagRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:GetRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy"], Resource = local.roles },
      { Sid = "PassExecutionRoles", Effect = "Allow", Action = ["iam:PassRole"], Resource = local.roles,
      Condition = { StringEquals = { "iam:PassedToService" = ["lambda.amazonaws.com", "scheduler.amazonaws.com"] } } },
      { Sid = "Schedules", Effect = "Allow", Action = ["scheduler:*"], Resource = [
        "arn:aws:scheduler:us-east-1:${local.account}:schedule-group/${local.prefix}-crawler-schedules",
        "arn:aws:scheduler:us-east-1:${local.account}:schedule/${local.prefix}-crawler-schedules/*"
      ] }
    ]
  })
}

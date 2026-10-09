# FirstName Crawler Infrastructure

Dev-only Terraform targets account `499133675835`, region `us-east-1`.
Resources use `firstname-dev-*`. Existing Baskt resources are not managed.
The existing FirstName state bucket and shared GitHub OIDC provider are retained.

## Configuration

**`dev/environment.tf` contains all application environment variables.**
Depth is 4, worker lease is 240 seconds, processing attempts remain 2, and
there are no per-page link or total discovered-page limits.
Lambda supplies `AWS_REGION`; table names and queue URLs are derived by Terraform.

## Deployment

`.github/workflows/terraform-dev.yml` validates tests and Terraform, creates the
ECR repository, builds the Lambda image, then applies the full dev stack using
the immutable image digest. Pushes to `dev` and `feature/*` deploy to GitHub's
`dev` environment. Pull requests validate without AWS access. Production is
not configured yet.

GitHub's `dev` environment needs `AWS_REGION=us-east-1` and
`AWS_ROLE_ARN=arn:aws:iam::499133675835:role/firstname-dev-github-deploy`.
OIDC replaces long-lived AWS credentials.

The ECR-only targeted apply bootstraps the repository before the first image
can be uploaded. The following full apply manages the complete stack.

`admin/` is a one-time administrator-managed Terraform stack that adds the
deployment permissions and a runtime permissions boundary. CI cannot change
that boundary or access the administrator's Terraform state. Apply administrator
permission changes locally using an authorized AWS profile before deploying CI.

Runtime IAM baselines in `dev/iam/generated/` were generated with IAM Policy
Autopilot 0.3.0. `dev/iam.tf` filters unused actions and scopes the resulting
policies to dedicated resources. Regenerate and review these when SDK calls change.

State keys are isolated:

- Crawler: `web-crawler/dev/terraform.tfstate`
- Administrator permissions: `web-crawler/admin/terraform.tfstate`
- Earlier job tables, left untouched: `dev/terraform.tfstate`

## Resources

Six DynamoDB tables, private versioned S3 artifacts, SQS queue and DLQ, ECR,
two container Lambdas, scoped execution roles, CloudWatch logs, SQS event source
mappings, and EventBridge Scheduler refresh/recovery schedules.

Schedules are disabled by default and the workflow explicitly keeps them
disabled. To enable crawling later, change the workflow's `TF_VAR_schedules_enabled`
to `true` and redeploy. Refresh runs at 02:00 America/New_York; recovery runs
every five minutes. No crawl is launched during deployment.

Do not apply the crawler stack using the old job-table state key. DynamoDB
tables and the artifact bucket are protected against Terraform destruction.

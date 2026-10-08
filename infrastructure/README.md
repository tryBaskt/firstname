# Development DynamoDB tables

Terraform in `dev/` defines two dev tables in Baskt account `499133675835`,
region `us-east-1`. Production, compute, schedules, and ingestion changes are
not included. All FirstName resources use `firstname-<env>-<purpose>` names.

The only shared AWS resource is the existing GitHub OIDC provider, referenced
read-only. `bootstrap/` manages FirstName's dedicated private, encrypted,
versioned state bucket and dev deployment role. It must be run with administrative
credentials; GitHub's role cannot manage IAM, the bucket configuration, or Baskt
resources. It cannot delete the dev tables or read/write their job items.

| Table | Partition key | Sort key |
| --- | --- | --- |
| `firstname-dev-job-post-sites` | `site_id` (string) | None |
| `firstname-dev-job-postings` | `site_id` (string) | `source_job_id` (string) |

Other attributes are flexible: sites can store `name`, `url`, `job_count`,
`initial_ingested_at`, and `last_successful_ingestion_at`. Jobs can store title,
company, location, application URL, and posting/ingestion timestamps.

Create a site record only after initial ingestion succeeds. No site records
are seeded by Terraform. Future ingestion code must make partial job writes
retry-safe using the composite key and handle overlapping runs. Terraform alone
does not enforce these application rules. A job found on two sources remains
two records. Query jobs by source; search indexes are deferred.

Both tables use on-demand capacity, default AWS-owned-key encryption, deletion
protection, and Terraform `prevent_destroy`. Dev point-in-time recovery is off.
No TTL is configured. Items must fit DynamoDB's 400 KB limit.

## Validate

Run from `infrastructure/dev/` with Terraform 1.10+:

```sh
AWS_PROFILE=siby_baskt terraform init
terraform fmt -check
terraform validate
terraform test
```

Tests use a mocked provider and create no AWS resources. Commit the provider
lock file, but never Terraform state, plans, or credentials.

## Plan

The dev region is fixed to `us-east-1`. Run:

```sh
AWS_PROFILE=siby_baskt terraform plan
```

State is stored in `firstname-dev-tfstate-499133675835-us-east-1` with S3-native
locking. The dev module uses `dev/terraform.tfstate`; bootstrap uses the separate
`bootstrap/terraform.tfstate` key. Neither state nor plan files belong in Git.
The GitHub role can access only the dev state and its lock, not bootstrap state.

## GitHub deployment

`.github/workflows/terraform-dev.yml` validates on pull requests and deploys on
infrastructure pushes to `dev` or `feature/*`. Manual runs are also available on
those branches. All feature branches share the same dev tables and state.
Feature names use one segment, e.g. `feature/add-sequoia`. Other branches and
tags cannot deploy. Production is intentionally not configured.

The GitHub `dev` environment permits only the `dev` and `feature/*` branch
patterns. Its non-secret variables are `AWS_REGION=us-east-1` and
`AWS_ROLE_ARN=arn:aws:iam::499133675835:role/firstname-dev-github-deploy`.
OIDC trust matches this repository's immutable ID-based subject and the `dev`
environment. No AWS access keys are stored on GitHub.

The workflow validates, runs mocked tests, and applies its saved Terraform plan.
Deployment jobs are serialized without interrupting a running apply; GitHub
may replace an older pending deployment with a newer pending one. S3 locking
also protects against overlapping local applies.

## Bootstrap maintenance

From `infrastructure/bootstrap/`, use `AWS_PROFILE=siby_baskt terraform init`,
then `AWS_PROFILE=siby_baskt terraform plan` to review changes. Bootstrap is
intentionally not auto-applied by GitHub. The bucket and role are dedicated to
FirstName; do not import or manage the existing OIDC provider in this module.
For a fresh account, create the bucket with local state before enabling its S3
backend, then migrate state. Never re-bootstrap this deployed setup with empty
local state.

Intentional deletion requires removing `prevent_destroy` and disabling AWS
deletion protection. Removing a resource block also removes its lifecycle guard.

Reference: [Terraform DynamoDB table](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/dynamodb_table).

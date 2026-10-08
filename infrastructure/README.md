# Development DynamoDB tables

Terraform in `dev/` defines only two dev tables in Baskt account `499133675835`.
No prod resources, GitHub deployment wiring, compute, schedules, or ingestion
changes are included.

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

Run from `infrastructure/dev/` with Terraform 1.7+:

```sh
terraform init
terraform fmt -check
terraform validate
terraform test
```

Tests use a mocked provider and create no AWS resources. Commit the provider
lock file, but never Terraform state, plans, or credentials.

## Plan

Select the AWS region explicitly by setting `TF_VAR_aws_region`, then run:

```sh
AWS_PROFILE=siby_baskt terraform plan
```

No apply has been run. State uses the local backend and is excluded from Git.
Preserve state after applying. Remote state and GitHub dev/prod deployment
routing are separate future steps. Do not use independent local state in CI.

Intentional deletion requires removing `prevent_destroy` and disabling AWS
deletion protection. Removing a resource block also removes its lifecycle guard.

Reference: [Terraform DynamoDB table](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/dynamodb_table).

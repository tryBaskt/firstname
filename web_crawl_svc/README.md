# FirstName Web Crawler Service

Internal Python workers migrated from the Profound crawler. No routes, HTTP API,
authentication, Cognito, Bedrock client, or llms.txt generator are included.

## Layout

- `infrastructure/dev/environment.tf`: all Lambda environment variables and crawler tuning.
- `infrastructure/dev/`: dedicated dev Terraform for the crawler's AWS resources.
- `clients/`: DynamoDB, S3, and SQS wrappers.
- `tables/`: sites, crawl runs, and crawl pages.
- `services/crawl_setup.py`: transactional site, run, and root-page setup.
- `services/crawl_and_parse.py`: fetching, parsing, discovery, checkpoints,
  worker leases, retries, recovery, and crawl completion.
- `services/nightly_refresh.py`: repeatable scheduled crawl setup.
- `handler.py`: SQS crawler, dead-letter processing, and recovery handler.
- `refresh_handler.py`: scheduled refresh handler.
- `tests/`: migrated crawler-focused mock tests and completion tests.

## Lambda Entry Points

Package `web_crawl_svc/` at the deployment root with its dependencies. The
handlers are `web_crawl_svc.handler.lambda_handler` and
`web_crawl_svc.refresh_handler.lambda_handler`. Existing deployables and
Terraform have not been rewired to these handlers.

The crawler accepts the existing `crawl_url` SQS contract; setup produces its
message. Scheduled refresh accepts `action=nightly_refresh` and a timezone-aware
ISO `scheduled_time`. Recovery accepts `action=recover_crawls`.

## Configuration

Shared configuration: `AWS_REGION`, `SITES_TABLE`, `CRAWL_RUNS_TABLE`,
`CRAWL_PAGES_TABLE`, and `CRAWL_QUEUE_URL`.

Crawler-only configuration: `APPLICATION_S3_BUCKET`, `CRAWL_DLQ_ARN`,
`JOB_POSTS_TABLE`,
`JOB_BOARDS_TABLE`, `JOB_POST_JOB_BOARDS_TABLE`,
`CRAWL_DLQ_URL`, `CRAWLER_RETRY_DELAY_SECONDS`, `CRAWLER_LEASE_SECONDS`,
`PARSER_VERSION`, `CRAWLER_MAX_DEPTH`, `CRAWLER_USER_AGENT`,
`CRAWLER_REQUEST_TIMEOUT_SECONDS`, `CRAWLER_MAX_RESPONSE_BYTES`, and
`CRAWLER_MAX_ATTEMPTS`.

`CRAWLER_MAX_LINKS_PER_PAGE` and `CRAWLER_MAX_DISCOVERED_PAGES` are optional;
when omitted there is no link-per-page or total discovered-page cap. Dev uses
depth 4 and a 240-second lease. Domain, depth, response-size, and sitemap-document
safety limits still apply; removing page caps can increase crawl time and cost.

AWS credentials come from the SDK's default credential chain, including the
Lambda execution role. No credentials or original `.env` were copied.
Use dedicated `firstname-dev-*` resources, not the original project's tables,
queues, or bucket. This migration does not provision those resources.

## Lifecycle

Pages retain `QUEUED -> CRAWLING_AND_PARSING -> COMPLETED | FAILED`; retryable
failures return to `QUEUED`. Existing processing-attempt and child-registration
attempt limits are unchanged. Runs retain their initial `PENDING` state.

The same discovery lock protects child registration and the completion check.
Once all pages are terminal, the run becomes `COMPLETED` if at least one page
succeeded, or `FAILED` if all pages failed. Completion stores success/failure
counts and `incomplete=true` when any page failed. It means crawling is finished,
not that job ingestion or deduplication has finished.

The generator queue and version-tracking lifecycle have been removed. No
deduplication handoff is implemented yet.

## Employer Job Pages

Discovery permits in-root links and recognized Ashby, Greenhouse, and Lever
board/job links outside the root site. Direct individual job links can be followed
at the depth limit as the final hop. Unrelated external sites and known ATS
application-form paths are excluded. Embedded JSON job URLs are discovered too.

Individual supported ATS job URLs are verified through their public APIs before
being parsed and saved with `is_job_post=true`,
`ats_provider`, `employer_slug`, and `employer_job_id`. No children or sitemap
URLs are enqueued from these pages. Company job boards remain non-job pages.
Ashby verification matches the discovered job against its company's API jobUrl;
Greenhouse and Lever use individual-job lookups and check the returned ID. The
verified API title, description, location, and full job payload are stored in
parsed JSON. Verification checkpoints are per run/page in S3 and reused on retry;
parsed object keys include the API payload hash to avoid reusing stale job data.

An explicit API 404 or a valid Ashby response without a match does not mark the
page as a job. Other HTTP failures, invalid responses, ID mismatches, and missing
title/description remain retryable. The existing HTML fetch still precedes API
verification; this change does not provide browser rendering or bypass failed
HTML requests. Lever EU routing is supported; EU Greenhouse verification is
explicitly deferred with a retryable error rather than querying the wrong region.
Custom ATS domains are not recognized yet. No application form is submitted.

## JobPosts

After saving verified API data to S3, the crawler inserts a canonical job into
`JOB_POSTS_TABLE` before marking the page parsed/completed. Its `job_post_id` is
a deterministic hash of ATS provider, employer slug, and employer job ID.
A conditional insert prevents both concurrent workers and retries from replacing
an existing record. Existing records are not updated by this step.

Records contain the ATS identity, title, description, location when available,
employer job URL, live flag, timestamps, and parsed S3 JSON reference. Descriptions
above 200 KB use the S3 reference rather than exceeding DynamoDB item limits.
Full API payloads remain in S3. Storage errors propagate into existing retries;
duplicate-record condition failures are harmless. Ongoing job content updates
are not implemented here.

## JobBoards and Relationships

Each confirmed job also creates/updates its source JobBoard, identified by the
crawl's `site_id`. Names come from site metadata when available, otherwise the
site ID; the board URL comes from the site's root URL. Relationships connect
that board to the canonical job, recording the employer job URL, discovery
parent URL when available, original crawl, and last-seen crawl/timestamp.

New links and board job-count increments are one DynamoDB transaction. Duplicate
links update last-seen metadata without incrementing the count. The same job can
belong to several boards without duplicating JobPosts. These are discovered
associations, not a claim that the VC board originally published the ATS listing.
`num_job_posts` counts unique stored associations, not current live openings;
closure reconciliation and link deletion are not implemented. Boards are created
when their first confirmed job is stored, not merely when a crawl starts.

## Migration Scope

Generator/auth/API files, integration tests targeting
the original application, caches, and environment files were not migrated.
No application code, tests, builds, or deployments were run during migration.

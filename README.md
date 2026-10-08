# FirstName

Startup jobs with school and previous-employer connections.

## Planned workflow

1. Aggregate jobs from VC portfolio job boards.
2. Accept an applicant's schools and previous employers.
3. Use Gemini with Google Search grounding to discover potential employee matches.
4. Research matching people's public personal sites and professional bios.
5. Display supported shared-background matches and publicly listed professional contact channels with source links.

Verify identities and current employment before labeling matches as confirmed. Missing contact details remain missing.

## Infrastructure target

- GitHub organization: `tryBaskt`
- Intended repository: `firstname` (public)
- AWS account: `499133675835`
- Local AWS profile: `siby_baskt`
- AWS region: not selected yet

AWS identity has been verified. GitHub repository creation and deployment are pending. No AWS infrastructure has been deployed.

Terraform for the two development DynamoDB tables is in `infrastructure/dev/`.
See `infrastructure/README.md` for the schema and validation commands. Production
and GitHub dev/prod routing are deferred. Ingestion still uses local SQLite;
connecting the Python batches to DynamoDB is separate.

## Credentials

Gemini requires Google API access separately from AWS. Configure its key through a secret store or server-side environment configuration; never commit it or send it to the browser.

## Python batch jobs

Requires Python 3.10+ on macOS or Linux. Uses the standard library only.
Run these commands from the repository directory.

One-time initial import of every a16z portfolio listing:

```sh
python3 -m firstname_jobs initial
```

Subsequent batch to insert newly discovered listings:

```sh
python3 -m firstname_jobs new
```

Both batches read the public company directory and each hiring company's job
pages from https://jobs.a16z.com/jobs, avoiding the global 100-page limit. Three
workers collect these partitions, and the combined count must match the board.
The public board has
no verified change feed, so the recurring batch compares stable source IDs and
inserts only unseen jobs. It does not overwrite existing jobs or remove missing
ones. This also catches older listings that appear on the board later.

Jobs and successful batch history are stored in `data/jobs.sqlite3`, excluded
from Git. Each job retains its complete source data in `raw_json`. The import
checks pagination and the exact unique job count before committing. Failed
imports preserve the previous data, and a file lock prevents overlapping runs.
The initial command refuses to run again after a successful baseline import.
This imports listing data supplied by the a16z board. Full descriptions on
individual companies' application pages are not fetched in this step.

Tests:

```sh
python3 -m unittest discover -s tests -v
```

Scheduling the `new` batch is a separate step. No recurring schedule is active.
The website, Gemini integration, and cloud deployment are not implemented yet.
Earlier JavaScript and infrastructure drafts remain in the repository but are
not part of this Python workflow.

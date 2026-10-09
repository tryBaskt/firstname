# FirstName

Internal job-board crawler that follows VC portfolio job links and verifies
individual employer postings through Ashby, Greenhouse, and Lever public APIs.
Verified API data is persisted with deterministic employer job identities.

## Development

Python 3.12 or newer:

```sh
python3 -m venv .venv
.venv/bin/pip install -e './web_crawl_svc[dev]'
.venv/bin/pytest web_crawl_svc/tests
```

## Deployment

GitHub Actions builds the Lambda container image and deploys the dev Terraform
stack for pushes to `dev` or `feature/*`. Recurring schedules remain disabled
until explicitly enabled. No public routes or APIs are deployed.

See [crawler infrastructure](web_crawl_svc/infrastructure/README.md).
Application environment variables are in
`web_crawl_svc/infrastructure/dev/environment.tf`.

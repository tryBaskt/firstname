"""Share transactional crawl setup between user requests and scheduled refreshes."""

from hashlib import sha256
from typing import Any

from web_crawl_svc.tables.crawl_pages import CrawlPagesTable
from web_crawl_svc.tables.crawl_runs import CrawlRunsTable
from web_crawl_svc.tables.sites import SitesTable


class CrawlSetupService:
    """Prepare the site, run, and root page before publishing a crawl message."""

    def __init__(
        self, *, sites: SitesTable, crawl_runs: CrawlRunsTable, crawl_pages: CrawlPagesTable
    ) -> None:
        """Store the site, run, and page table wrappers without performing I/O."""
        self.sites = sites
        self.crawl_runs = crawl_runs
        self.crawl_pages = crawl_pages

    def prepare(
        self,
        *,
        site_id: str,
        root_url: str,
        crawl_run_id: str,
        timestamp: str,
        resume_existing: bool = False,
    ) -> dict[str, Any] | None:
        """Prepare workflow records and return the root-page SQS message to publish.

        Hash the supplied root_url as-is for the root page key. For a new run,
        atomically apply the site setup update, create a PENDING run, and create
        its depth-zero QUEUED page using the caller's IDs and timestamp.

        With resume_existing, reuse a matching PENDING run without rewriting its
        records; return None if that run has already advanced. This checks only
        the supplied crawl_run_id, not other active runs for the site. Transaction
        errors propagate. The caller sends the returned message after successful
        setup; this method neither publishes to SQS nor writes user membership.
        """
        canonical_url_hash = sha256(root_url.encode()).hexdigest()
        existing_run = (
            self.crawl_runs.get(site_id=site_id, crawl_run_id=crawl_run_id)
            if resume_existing
            else None
        )
        if existing_run is not None and existing_run.get("status") != "PENDING":
            return None

        if existing_run is None:
            run = CrawlRunsTable.new_item(
                site_id=site_id,
                crawl_run_id=crawl_run_id,
                created_at=timestamp,
                root_url=root_url,
            )
            root = CrawlPagesTable.new_item(
                crawl_run_id=crawl_run_id,
                canonical_url_hash=canonical_url_hash,
                site_id=site_id,
                url=root_url,
                root_url=root_url,
                depth=0,
                created_at=timestamp,
            )
            site_write = self.sites.crawl_setup_write(
                site_id=site_id,
                root_url=root_url,
                crawl_run_id=crawl_run_id,
                timestamp=timestamp,
            )
            self.crawl_pages.initialize_run(
                run, root, self.crawl_runs.dynamodb.table_name, site_write
            )

        return {
            "action": "crawl_url",
            "payload": {
                "site_id": site_id,
                "crawl_run_id": crawl_run_id,
                "root_url": root_url,
                "url": root_url,
                "canonical_url_hash": canonical_url_hash,
                "depth": 0,
            },
        }

"""Start scheduled site crawls using shared setup and stable per-occurrence run IDs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from web_crawl_svc.clients.sqs import SQSClient
from web_crawl_svc.services.crawl_setup import CrawlSetupService
from web_crawl_svc.tables.crawl_pages import CrawlPagesTable
from web_crawl_svc.tables.crawl_runs import CrawlRunsTable
from web_crawl_svc.tables.sites import SitesTable


class NightlyRefreshError(Exception):
    """Raised when one or more sites cannot be queued for nightly refresh."""


@dataclass(frozen=True)
class NightlyRefreshResult:
    """Summarize listed sites, accepted queue sends, and already-advanced runs.

    Counts describe setup/dispatch outcomes, not completed crawl work.
    """

    discovered_sites: int
    queued_sites: int
    skipped_sites: int


class NightlyRefreshService:
    """Start one retry-safe crawl run for every registered site."""

    def __init__(
        self,
        *,
        sites: SitesTable,
        crawl_runs: CrawlRunsTable,
        crawl_pages: CrawlPagesTable,
        crawl_queue: SQSClient,
    ) -> None:
        """Bind table wrappers and the crawl queue without contacting AWS."""
        self.sites = sites
        self.crawl_runs = crawl_runs
        self.crawl_pages = crawl_pages
        self.crawl_queue = crawl_queue

    def refresh_all(self, *, scheduled_time: str) -> NightlyRefreshResult:
        """Attempt to queue every Sites record for one scheduled occurrence.

        Require a timezone-aware ISO scheduled_time, preserving its stripped
        string for run identity and setup timestamps. Continue after individual
        site failures, then raise an aggregate NightlyRefreshError if any failed;
        successful sends are not rolled back. Otherwise return site counts.
        Repeated invocations can resend PENDING runs but skip those already advanced.
        """
        if not scheduled_time.strip():
            raise NightlyRefreshError("scheduled_time is required")
        try:
            parsed_time = datetime.fromisoformat(scheduled_time.strip())
            if parsed_time.tzinfo is None:
                raise ValueError("timezone is required")
        except ValueError as error:
            raise NightlyRefreshError(
                "scheduled_time must be an ISO timestamp with timezone"
            ) from error

        site_records = self.sites.list_all()
        queued_sites = 0
        skipped_sites = 0
        failures: list[str] = []

        for site in site_records:
            try:
                if self._queue_site(site, scheduled_time.strip()):
                    queued_sites += 1
                else:
                    skipped_sites += 1
            except Exception as error:
                failures.append(f"{site.get('site_id', 'unknown')}: {error}")

        if failures:
            raise NightlyRefreshError(
                f"Failed to queue {len(failures)} site(s): {'; '.join(failures)}"
            )

        return NightlyRefreshResult(
            discovered_sites=len(site_records),
            queued_sites=queued_sites,
            skipped_sites=skipped_sites,
        )

    def _queue_site(self, site: dict[str, Any], scheduled_time: str) -> bool:
        """Prepare one site's scheduled run, then publish its root crawl message.

        Derive a stable run ID and call shared setup with resume_existing=True.
        Return False if this occurrence's run has advanced beyond PENDING;
        otherwise send after setup succeeds and return True. Missing fields or
        storage/queue failures propagate to refresh_all for aggregate reporting.
        A retry may resend a PENDING run, so duplicate messages remain possible.
        """
        site_id = _required_site_value(site, "site_id")
        root_url = _required_site_value(site, "root_url")
        crawl_run_id = _scheduled_crawl_run_id(site_id, scheduled_time)
        message = CrawlSetupService(
            sites=self.sites, crawl_runs=self.crawl_runs, crawl_pages=self.crawl_pages
        ).prepare(
            site_id=site_id,
            root_url=root_url,
            crawl_run_id=crawl_run_id,
            timestamp=scheduled_time,
            resume_existing=True,
        )
        if message is None:
            return False
        self.crawl_queue.send_json(message)
        return True


def _scheduled_crawl_run_id(site_id: str, scheduled_time: str) -> str:
    """Hash the exact scheduled-time string and site ID into a repeatable crawl ID.

    Retries with identical inputs reuse the run. Equivalent timestamps written
    differently are not normalized here and therefore produce different IDs.
    """
    digest = sha256(f"{scheduled_time}:{site_id}".encode()).hexdigest()
    return f"crawl_{digest}"


def _required_site_value(site: dict[str, Any], key: str) -> str:
    """Return a stripped site field or raise NightlyRefreshError if missing/blank."""
    value = site.get(key)
    if not isinstance(value, str) or not value.strip():
        raise NightlyRefreshError(f"Site record is missing {key}")
    return value.strip()

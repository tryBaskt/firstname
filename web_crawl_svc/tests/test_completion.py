from unittest.mock import Mock

import pytest

from web_crawl_svc.clients.dynamodb import DynamoDBConditionNotMetError
from web_crawl_svc.services.crawl_and_parse import CrawlAndParseService
from web_crawl_svc.tables.crawl_pages import CrawlPagesTable
from web_crawl_svc.tables.crawl_runs import CrawlRunsTable


@pytest.fixture
def service():
    runs = Mock()
    runs.get.return_value = {"status": "CRAWLING_AND_PARSING"}
    runs.acquire_discovery_lock.return_value = True
    return CrawlAndParseService(
        crawl_pages=Mock(),
        crawl_runs=runs,
        sites=Mock(),
        job_posts=Mock(),
        job_boards=Mock(),
        job_post_job_boards=Mock(),
        s3=Mock(),
        crawl_queue=Mock(),
        user_agent="test",
        request_timeout_seconds=1,
        max_response_bytes=1000,
        max_attempts=2,
        retry_delay_seconds=30,
        lease_seconds=150,
        parser_version="v2",
        max_depth=2,
        max_links_per_page=100,
        max_discovered_pages=1000,
    )


@pytest.mark.parametrize("status", ["QUEUED", "CRAWLING_AND_PARSING"])
def test_completion_waits_for_nonterminal_pages(service, status):
    service.pages.list_for_run.return_value = [{"status": "COMPLETED"}, {"status": status}]
    service.check_completion({"site_id": "site", "crawl_run_id": "run"})
    service.runs.complete_crawl.assert_not_called()
    service.runs.release_discovery_lock.assert_called_once()


def test_completion_requires_discovery_lock(service):
    service.runs.acquire_discovery_lock.return_value = False
    service.check_completion({"site_id": "site", "crawl_run_id": "run"})
    service.pages.list_for_run.assert_not_called()
    service.runs.complete_crawl.assert_not_called()


def test_completion_records_successes_and_failures_without_queue_dispatch(service):
    service.pages.list_for_run.return_value = [{"status": "COMPLETED"}, {"status": "FAILED"}]
    service.check_completion({"site_id": "site", "crawl_run_id": "run"})
    arguments = service.runs.complete_crawl.call_args.kwargs
    assert arguments["completed_page_count"] == 1
    assert arguments["failed_page_count"] == 1
    service.crawl_queue.send_json.assert_not_called()


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED"])
def test_terminal_run_is_not_completed_again(service, status):
    service.runs.get.return_value = {"status": status}
    service.check_completion({"site_id": "site", "crawl_run_id": "run"})
    service.runs.acquire_discovery_lock.assert_not_called()


@pytest.mark.parametrize(
    "completed,failed,status,incomplete",
    [(2, 0, "COMPLETED", False), (1, 1, "COMPLETED", True), (0, 2, "FAILED", True)],
)
def test_run_completion_stores_crawl_outcome(completed, failed, status, incomplete):
    client = Mock()
    assert CrawlRunsTable(client).complete_crawl(
        site_id="site",
        crawl_run_id="run",
        token="owner",
        updated_at="2026-10-08T00:00:00+00:00",
        completed_page_count=completed,
        failed_page_count=failed,
    )
    values = client.update_item.call_args.kwargs["expression_attribute_values"]
    assert values[":status"] == status
    assert values[":incomplete"] is incomplete


def test_stale_completion_claim_is_harmless():
    client = Mock()
    client.update_item.side_effect = DynamoDBConditionNotMetError("ownership changed")
    assert not CrawlRunsTable(client).complete_crawl(
        site_id="site",
        crawl_run_id="run",
        token="stale",
        updated_at="2026-10-08T00:00:00+00:00",
        completed_page_count=1,
        failed_page_count=0,
    )


def test_page_still_starts_queued_with_existing_attempt_budgets():
    page = CrawlPagesTable.new_item(
        site_id="site",
        crawl_run_id="run",
        canonical_url_hash="hash",
        url="https://example.com/",
        root_url="https://example.com/",
        depth=0,
        created_at="2026-10-08T00:00:00+00:00",
    )
    assert page["status"] == "QUEUED"
    assert page["attempt_count"] == 0
    assert not CrawlPagesTable.attempts_exhausted(page, 2)
    assert CrawlPagesTable.attempts_exhausted({**page, "attempt_count": 2}, 2)

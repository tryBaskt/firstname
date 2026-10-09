from unittest.mock import Mock

from web_crawl_svc.services.crawl_setup import CrawlSetupService
from web_crawl_svc.tables.crawl_runs import CrawlRunsTable
from web_crawl_svc.tables.sites import SitesTable


def test_latest_run_is_published_after_run_and_root_page_exist():
    operations = Mock()
    service = CrawlSetupService(
        sites=operations.sites,
        crawl_runs=operations.runs,
        crawl_pages=operations.pages,
    )
    message = service.prepare(
        site_id="site-1",
        root_url="https://example.com/",
        crawl_run_id="scheduled-run",
        timestamp="2026-09-30T02:10:00Z",
    )
    assert [call[0] for call in operations.mock_calls] == [
        "sites.crawl_setup_write",
        "pages.initialize_run",
    ]
    assert message["payload"]["crawl_run_id"] == "scheduled-run"


def test_status_lookups_use_strongly_consistent_reads():
    client = Mock()
    SitesTable(client).get("site-1")
    client.get_item.assert_called_with(key={"site_id": "site-1"}, consistent_read=True)
    CrawlRunsTable(client).get(site_id="site-1", crawl_run_id="run-1")
    client.get_item.assert_called_with(
        key={"site_id": "site-1", "crawl_run_id": "run-1"},
        consistent_read=True,
    )

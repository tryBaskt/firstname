from unittest.mock import Mock

from lxml import html

from web_crawl_svc.services.crawl_and_parse import CrawlAndParseService, should_discover_url
from web_crawl_svc.services.job_sites import embedded_job_urls, identify_job_site

JOB_ID = "b94099f6-8418-48b0-82b5-5953a27d636f"


def test_boards_and_jobs_are_distinct():
    assert identify_job_site("https://jobs.ashbyhq.com/openai").job_id is None
    assert identify_job_site(f"https://jobs.ashbyhq.com/openai/{JOB_ID}").job_id == JOB_ID
    assert identify_job_site("https://job-boards.greenhouse.io/example/jobs/123").job_id == "123"
    assert identify_job_site(f"https://jobs.eu.lever.co/example/{JOB_ID}").job_id == JOB_ID


def test_external_job_allowed_at_depth_limit_but_forms_and_other_hosts_excluded():
    root = "https://jobs.a16z.com/"
    job = f"https://jobs.ashbyhq.com/openai/{JOB_ID}"
    assert should_discover_url(job, root, depth=4, max_depth=4)
    assert not should_discover_url(job + "/application", root, depth=1, max_depth=4)
    assert not should_discover_url(f"https://jobs.lever.co/example/{JOB_ID}/apply", root,
                                   depth=1, max_depth=4)
    assert not should_discover_url("https://other.test/jobs/123", root, depth=1, max_depth=4)
    assert identify_job_site(job.replace("ashbyhq.com", "ashbyhq.com.evil.test")) is None


def test_embedded_json_employer_link_is_discovered():
    job = f"https://jobs.ashbyhq.com/openai/{JOB_ID}"
    document = html.fromstring(f'<script type="application/json">{{"url":"{job}"}}</script>')
    assert embedded_job_urls(document) == [job]


def test_ats_job_is_parsed_and_saved_without_children():
    s3, pages = Mock(), Mock()
    s3.object_exists.return_value = False
    s3.get_bytes.return_value = b'''<html><title>Engineer</title>
    <script type="application/ld+json">{"@type":"JobPosting","title":"Engineer",
    "description":"Build software","hiringOrganization":{"name":"OpenAI"}}</script>
    <a href="https://jobs.ashbyhq.com/openai">Other jobs</a></html>'''
    service = CrawlAndParseService(
        crawl_pages=pages, crawl_runs=Mock(), sites=Mock(), job_posts=Mock(),
        s3=s3, crawl_queue=Mock(),
        job_boards=Mock(), job_post_job_boards=Mock(),
        user_agent="test", request_timeout_seconds=1, max_response_bytes=1000,
        max_attempts=2, retry_delay_seconds=30, lease_seconds=240, parser_version="v2",
        max_depth=4, max_links_per_page=None, max_discovered_pages=None,
        job_verifier=Mock(return_value={
            "title": "Engineer", "description": "Build software",
            "ats_provider": "ashby", "employer_slug": "openai", "employer_job_id": JOB_ID,
        }),
    )
    page = dict(site_id="site", crawl_run_id="run", canonical_url_hash="hash", depth=1,
                raw_html_s3_key="raw.html", raw_html_hash="hash", content_type="text/html",
                final_url=f"https://jobs.ashbyhq.com/openai/{JOB_ID}",
                root_url="https://jobs.a16z.com/")
    assert service._crawl_and_parse(page, "worker") == []
    saved = pages.save_result.call_args.kwargs["result"]
    assert saved["is_job_post"] is True
    assert saved["employer_job_id"] == JOB_ID
    assert saved["ats_provider"] == "ashby"
    service.job_posts.create_if_absent.assert_called_once()
    service.job_boards.upsert.assert_called_once()
    service.job_post_job_boards.link_if_absent.assert_called_once()
    link = service.job_post_job_boards.link_if_absent.call_args.kwargs
    assert link["job_board_id"] == "site"

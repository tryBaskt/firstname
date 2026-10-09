import json
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from web_crawl_svc.services.ats_api import ATSAPIError, fetch_api_json, verify_job

JOB_ID = "b94099f6-8418-48b0-82b5-5953a27d636f"


def verify(url, response):
    fetch = Mock(return_value=response)
    return verify_job(url, user_agent="test", timeout_seconds=1,
                      max_response_bytes=1000, fetch_json=fetch), fetch


def test_ashby_matches_job_url_and_preserves_payload():
    url = f"https://jobs.ashbyhq.com/openai/{JOB_ID}"
    job = {"jobUrl": url, "title": "Engineer", "descriptionPlain": "Build software"}
    result, fetch = verify(url + "?utm_source=vc", {"jobs": [job]})
    assert result["employer_job_id"] == JOB_ID
    assert result["job_posting"] == job
    assert fetch.call_args.args[0] == "https://api.ashbyhq.com/posting-api/job-board/openai"


def test_ashby_no_match_is_not_verified():
    result, _ = verify(f"https://jobs.ashbyhq.com/openai/{JOB_ID}", {"jobs": []})
    assert result is None


def test_greenhouse_verifies_specific_id():
    result, fetch = verify("https://job-boards.greenhouse.io/company/jobs/123",
                           {"id": 123, "title": "Engineer", "content": "Build software"})
    assert result["employer_job_id"] == "123"
    assert fetch.call_args.args[0].endswith("/v1/boards/company/jobs/123")


def test_lever_uses_eu_endpoint_and_checks_id():
    result, fetch = verify(f"https://jobs.eu.lever.co/company/{JOB_ID}",
                           {"id": JOB_ID, "text": "Engineer", "description": "Build software"})
    assert result["title"] == "Engineer"
    assert fetch.call_args.args[0].startswith("https://api.eu.lever.co/")


@pytest.mark.parametrize("payload", [{}, {"id": 456}, {"id": 123, "title": "Engineer"}])
def test_bad_api_data_is_retryable_not_a_non_job(payload):
    with pytest.raises(ATSAPIError):
        verify("https://job-boards.greenhouse.io/company/jobs/123", payload)


def test_boards_and_application_forms_do_not_call_api():
    for url in ["https://jobs.ashbyhq.com/openai",
                f"https://jobs.ashbyhq.com/openai/{JOB_ID}/application"]:
        result, fetch = verify(url, {})
        assert result is None
        fetch.assert_not_called()


@pytest.mark.parametrize("status", [404, 403, 429, 500])
def test_http_failure_semantics(monkeypatch, status):
    opener = Mock()
    opener.open.side_effect = HTTPError("https://api.lever.co/", status, "error", {}, None)
    monkeypatch.setattr("web_crawl_svc.services.ats_api.build_opener", Mock(return_value=opener))
    arguments = dict(user_agent="test", timeout_seconds=1, max_response_bytes=1000)
    if status == 404:
        assert fetch_api_json("https://api.lever.co/", **arguments) is None
    else:
        with pytest.raises(ATSAPIError):
            fetch_api_json("https://api.lever.co/", **arguments)


def test_api_response_size_is_bounded(monkeypatch):
    response = Mock()
    response.read.return_value = json.dumps({"data": "too long"}).encode()
    opener = Mock()
    opener.open.return_value.__enter__ = Mock(return_value=response)
    opener.open.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("web_crawl_svc.services.ats_api.build_opener", Mock(return_value=opener))
    with pytest.raises(ATSAPIError):
        fetch_api_json("https://api.lever.co/", user_agent="test", timeout_seconds=1,
                       max_response_bytes=5)

"""Verify individual employer postings through public, read-only ATS APIs."""

import json
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from web_crawl_svc.services.job_sites import identify_job_site


class ATSAPIError(Exception):
    """An unavailable or malformed API response must not imply job absence."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_api_json(url, *, user_agent, timeout_seconds, max_response_bytes):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": user_agent})
    try:
        with build_opener(_NoRedirects()).open(request, timeout=timeout_seconds) as response:
            payload = response.read(max_response_bytes + 1)
            if len(payload) > max_response_bytes:
                raise ATSAPIError("ATS API response exceeds the configured byte limit")
            return json.loads(payload)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise ATSAPIError(f"ATS API returned HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise ATSAPIError(f"Could not read ATS API response: {error}") from error


def verify_job(url, *, user_agent, timeout_seconds, max_response_bytes, fetch_json=fetch_api_json):
    """Return normalized verified job data, or None for an explicit absence.

    Board and application URLs never trigger API calls. API faults and identity
    mismatches raise retryable errors. Endpoint hosts are fixed, not page supplied.
    """
    ats = identify_job_site(url)
    if not ats or not ats.job_id or ats.is_application:
        return None
    company, job_id = quote(ats.employer, safe=""), quote(ats.job_id, safe="")
    if ats.provider == "ashby":
        endpoint = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
    elif ats.provider == "greenhouse":
        if ".eu.greenhouse.io" in (urlsplit(url).hostname or ""):
            raise ATSAPIError("EU Greenhouse API routing is not configured")
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"
    else:
        host = "api.eu.lever.co" if urlsplit(url).hostname == "jobs.eu.lever.co" else "api.lever.co"
        endpoint = f"https://{host}/v0/postings/{company}/{job_id}?mode=json"
    data = fetch_json(endpoint, user_agent=user_agent, timeout_seconds=timeout_seconds,
                      max_response_bytes=max_response_bytes)
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ATSAPIError("ATS API response must be an object")
    if ats.provider == "ashby":
        jobs = data.get("jobs")
        if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
            raise ATSAPIError("Ashby API response has no valid jobs list")
        matches = []
        for job in jobs:
            job_url = job.get("jobUrl")
            if not isinstance(job_url, str):
                raise ATSAPIError("Ashby job is missing a valid jobUrl")
            candidate = identify_job_site(job_url)
            if candidate and candidate.provider == ats.provider and candidate.employer == ats.employer \
                    and candidate.job_id == ats.job_id and not candidate.is_application:
                matches.append(job)
        if not matches:
            return None
        if len(matches) != 1:
            raise ATSAPIError("Ashby returned multiple matching jobs")
        data = matches[0]
        title = data.get("title")
        description = data.get("descriptionHtml") or data.get("descriptionPlain")
        location = data.get("location")
    elif ats.provider == "greenhouse":
        if str(data.get("id")) != ats.job_id:
            raise ATSAPIError("Greenhouse returned a different job ID")
        title, description = data.get("title"), data.get("content")
        location = data.get("location")
    else:
        if data.get("id") != ats.job_id:
            raise ATSAPIError("Lever returned a different job ID")
        title = data.get("text")
        description = data.get("description") or data.get("descriptionPlain")
        categories = data.get("categories", {})
        if not isinstance(categories, dict):
            raise ATSAPIError("Lever categories must be an object")
        location = categories.get("location")
    if not isinstance(title, str) or not title.strip():
        raise ATSAPIError("ATS API job has no title")
    if not isinstance(description, str) or not description.strip():
        raise ATSAPIError("ATS API job has no description")
    return {
        "title": title,
        "description": description,
        "location": location,
        "ats_provider": ats.provider,
        "employer_slug": ats.employer,
        "employer_job_id": ats.job_id,
        "job_posting": data,
        "verification_source": endpoint,
    }


def verification_hash(result):
    return sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

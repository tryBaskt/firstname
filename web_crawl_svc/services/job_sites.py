"""Recognize supported ATS boards, job descriptions, and application forms."""

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "boards.eu.greenhouse.io": "greenhouse",
    "job-boards.eu.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
}


@dataclass(frozen=True)
class JobSitePage:
    provider: str
    employer: str
    job_id: str | None
    is_application: bool = False


def identify_job_site(url: str) -> JobSitePage | None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        if parsed.port not in {None, 80, 443}:
            return None
    except ValueError:
        return None
    provider = HOSTS.get((parsed.hostname or "").lower())
    if provider is None:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    employer = parts[0]
    if len(parts) == 1:
        return JobSitePage(provider, employer, None)
    if provider == "greenhouse":
        if len(parts) == 3 and parts[1] == "jobs" and parts[2].isdigit():
            return JobSitePage(provider, employer, parts[2])
        return None
    if re.fullmatch(UUID, parts[1]):
        if len(parts) == 2:
            return JobSitePage(provider, employer, parts[1])
        form_path = "application" if provider == "ashby" else "apply"
        if len(parts) == 3 and parts[2] == form_path:
            return JobSitePage(provider, employer, parts[1], is_application=True)
    return None


def job_posting_data(document) -> dict | None:
    """Use a single explicit JobPosting record, not a multi-job listing."""
    postings = []

    def visit(value):
        if isinstance(value, dict):
            types = value.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if isinstance(types, list) and "JobPosting" in types:
                postings.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for text in document.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            visit(json.loads(text))
        except (ValueError, TypeError):
            continue
    return postings[0] if len(postings) == 1 else None


def embedded_job_urls(document) -> list[str]:
    """Find ATS links in embedded JSON, including links not rendered as anchors."""
    urls = set()

    def visit(value):
        if isinstance(value, str):
            candidate = identify_job_site(value)
            if candidate and candidate.job_id and not candidate.is_application:
                urls.add(value)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for text in document.xpath("//script[not(@src)]/text()"):
        try:
            visit(json.loads(text))
        except (ValueError, TypeError):
            # Framework scripts can wrap JSON in JavaScript rather than expose JSON.
            for candidate in re.findall(r'https?://[^\s"<>\\]+', text):
                visit(candidate)
    return sorted(urls)

"""Read every listing through the a16z board's public pagination request."""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

BOARD_URL = "https://jobs.a16z.com/jobs"
ORIGIN = "https://jobs.a16z.com"


def request(url, *, data=None, headers=None):
    request_headers = {"User-Agent": "FirstName-JobImporter/0.1", "Accept": "text/html"}
    request_headers.update(headers or {})
    for attempt in range(4):
        retry_seconds = 2 ** attempt
        try:
            with urlopen(Request(url, data=data, headers=request_headers), timeout=30) as response:
                return response.read()
        except HTTPError as error:
            status = error.code
            retry_after = error.headers.get("Retry-After")
            error.close()
            if status != 429 and status < 500:
                raise RuntimeError(f"HTTP {status} from {url}") from error
            if retry_after:
                try:
                    retry_seconds = max(retry_seconds, float(retry_after))
                except ValueError:
                    try:
                        retry_seconds = max(retry_seconds, parsedate_to_datetime(retry_after).timestamp() - time.time())
                    except (ValueError, TypeError):
                        pass
            if attempt == 3:
                raise RuntimeError(f"HTTP {status} after retries: {url}") from error
        except (URLError, TimeoutError) as error:
            if attempt == 3:
                raise RuntimeError(f"Request failed after retries: {url}") from error
        time.sleep(min(60, retry_seconds))


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []
        self.inline_scripts = []
        self.current_script = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            src = dict(attrs).get("src")
            if src and src not in self.sources:
                self.sources.append(src)
            self.current_script = [] if not src else None

    def handle_data(self, data):
        if self.current_script is not None:
            self.current_script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current_script is not None:
            self.inline_scripts.append("".join(self.current_script))
            self.current_script = None


def parse_companies(html):
    parser = ScriptParser()
    parser.feed(html)
    chunks = []
    for script in parser.inline_scripts:
        match = re.fullmatch(r"self[.]__next_f[.]push\((.*)\)", script, re.DOTALL)
        if match:
            value = json.loads(match[1])
            if value[0] == 1 and isinstance(value[1], str):
                chunks.append(value[1])

    def find_companies(value):
        if isinstance(value, dict):
            if isinstance(value.get("companies"), list):
                return value["companies"]
            children = value.values()
        elif isinstance(value, list):
            children = value
        else:
            return None
        for child in children:
            found = find_companies(child)
            if found is not None:
                return found
        return None

    for record in _decode_records("".join(chunks), ignore_control_records=True).values():
        companies = find_companies(record)
        if companies is not None:
            if (not companies or any(not isinstance(company.get("id"), str)
                    or type(company.get("jobCount")) is not int or company["jobCount"] < 0 for company in companies)
                    or len({company["id"] for company in companies}) != len(companies)):
                raise RuntimeError("Invalid a16z company directory.")
            return companies
    raise RuntimeError("a16z company directory data not found.")


def discover_action():
    parser = ScriptParser()
    parser.feed(request(BOARD_URL).decode("utf-8"))
    for source in parser.sources:
        url = urljoin(ORIGIN, source)
        parsed = urlparse(url)
        if parsed.netloc != "jobs.a16z.com" or not parsed.path.startswith("/_next/static/"):
            continue
        script = request(url).decode("utf-8")
        match = re.search(r'createServerReference\)\("([a-f0-9]+)"[^;]{0,400}"loadMorePublicJobs"\)', script)
        if match:
            return match.group(1)
    raise RuntimeError("a16z public pagination loader not found; the board may have changed.")


def _decode_records(data, *, ignore_control_records=False):
    """Decode Next.js JSON/text records without executing remote code."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    records = {}
    offset = 0
    while offset < len(data):
        if data[offset:offset + 1] == b"\n":
            offset += 1
            continue
        colon = data.find(b":", offset)
        record_id = data[offset:colon].decode("ascii")
        if colon < 0 or not re.fullmatch(r"[a-f0-9]*" if ignore_control_records else r"[a-f0-9]+", record_id):
            raise RuntimeError("Invalid job-response framing.")
        offset = colon + 1
        if data[offset:offset + 1] == b"T":
            comma = data.find(b",", offset)
            if comma < 0:
                raise RuntimeError("Invalid text record.")
            length = int(data[offset + 1:comma], 16)
            offset = comma + 1
            if offset + length > len(data):
                raise RuntimeError("Truncated text record.")
            records[record_id] = data[offset:offset + length].decode("utf-8")
            offset += length
        else:
            end = data.find(b"\n", offset)
            if end < 0:
                end = len(data)
            payload = data[offset:end]
            if payload.startswith(b"E"):
                raise RuntimeError("a16z returned a pagination error.")
            if not ignore_control_records or payload[:1] in (b'"', b'{', b'['):
                records[record_id] = json.loads(payload)
            offset = end + 1
    return records


def parse_response(data):
    records = _decode_records(data)

    def resolve(value, depth=0):
        if depth > 100:
            raise RuntimeError("Cyclic job-response reference.")
        if isinstance(value, str):
            if value.startswith("$$"):
                return value[1:]
            if value == "$undefined":
                return None
            match = re.fullmatch(r"\$(?:@)?([a-f0-9]+)(?::(.*))?", value)
            if match:
                if match[1] not in records:
                    raise RuntimeError("Missing job-response record.")
                target = records[match[1]]
                for key in match[2].split(":") if match[2] is not None else []:
                    target = target[int(key)] if isinstance(target, list) else target[key]
                return resolve(target, depth + 1)
            return value
        if isinstance(value, list):
            return [resolve(item, depth + 1) for item in value]
        if isinstance(value, dict):
            return {key: resolve(item, depth + 1) for key, item in value.items()}
        return value

    page = resolve(records.get("0", {}).get("a"))
    validate_page(page)
    return page


def validate_page(page):
    if (not isinstance(page, dict) or not isinstance(page.get("jobs"), list)
            or type(page.get("total")) is not int or page["total"] < 0
            or type(page.get("page")) is not int or page["page"] < 0
            or type(page.get("perPage")) is not int or page["perPage"] < 1
            or type(page.get("isDone")) is not bool or page.get("countKind") != "exact"):
        raise RuntimeError("Unexpected a16z page schema.")
    for job in page["jobs"]:
        if not isinstance(job, dict) or any(not isinstance(job.get(key), str) or not job[key] for key in ("id", "title", "company_name")):
            raise RuntimeError("Invalid a16z listing.")
        apply_url = job.get("apply_url")
        if apply_url is not None and (not isinstance(apply_url, str) or urlparse(apply_url).scheme not in ("http", "https", "mailto")):
            raise RuntimeError(f"Invalid application URL for {job['id']}.")


def fetch_page(action, page, limit, *, company_id=None):
    options = {"page": page, "limit": limit}
    if company_id is not None:
        options["companyId"] = company_id
    return parse_response(request(
        BOARD_URL,
        data=json.dumps([{"sort": "recent"}, options]).encode(),
        headers={"Next-Action": action, "Content-Type": "text/plain;charset=UTF-8", "Accept": "text/x-component", "Origin": ORIGIN},
    ))


def collect_jobs(get_page, *, delay=0.3, progress=lambda *_: None):
    jobs = {}
    total = None
    per_page = 100
    started = time.monotonic()
    for index in range(2000):
        if time.monotonic() - started > 15 * 60:
            raise RuntimeError("Import exceeded its time budget.")
        page = get_page(index, per_page)
        validate_page(page)
        if page["page"] != index:
            raise RuntimeError("Pagination did not advance.")
        if total is None:
            total = page["total"]
        if page["total"] != total:
            raise RuntimeError("Job count changed during pagination; retry the batch.")
        if index > 0 and page["perPage"] != per_page:
            raise RuntimeError("Page size changed during pagination.")
        per_page = page["perPage"]
        before = len(jobs)
        for job in page["jobs"]:
            jobs[job["id"]] = job
        progress(index, len(jobs), total)
        if page["isDone"]:
            if len(jobs) != total:
                raise RuntimeError(f"Incomplete import: {len(jobs)} unique listings, expected {total}.")
            if not jobs:
                raise RuntimeError("Refusing an unexpected empty job board.")
            return {"jobs": list(jobs.values()), "total": total, "pages": index + 1}
        if len(jobs) == before or len(page["jobs"]) != per_page:
            raise RuntimeError("Incomplete or repeated page.")
        if delay:
            time.sleep(delay)
    raise RuntimeError("Pagination exceeded the page limit.")


def read_all_jobs(*, progress=lambda *_: None):
    action = discover_action()
    total = fetch_page(action, 0, 100)["total"]
    companies = parse_companies(request(ORIGIN + "/companies").decode("utf-8"))
    if sum(company["jobCount"] for company in companies) != total:
        raise RuntimeError("Company counts do not match the full job board; retry the batch.")
    hiring = [company for company in companies if company["jobCount"] > 0]

    def read_company(company):
        result = collect_jobs(lambda page, limit: fetch_page(action, page, limit, company_id=company["id"]))
        if result["total"] != company["jobCount"]:
            raise RuntimeError(f"Job count changed for {company.get('name', company['id'])}; retry the batch.")
        if any(job.get("company_id") != company["id"] for job in result["jobs"]):
            raise RuntimeError("The public company filter returned another company's jobs.")
        return result

    jobs = {}
    pages = 0
    # Company partitions avoid the global 100-page cap. Keep concurrency modest.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(read_company, company) for company in hiring]
        try:
            for completed, future in enumerate(as_completed(futures), 1):
                result = future.result()
                pages += result["pages"]
                jobs.update((job["id"], job) for job in result["jobs"])
                progress(completed, len(jobs), total)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    final_total = fetch_page(action, 0, 100)["total"]
    if len(jobs) != total or final_total != total:
        raise RuntimeError(f"Incomplete or changed board: collected {len(jobs)}, expected {total}, now {final_total}.")
    return {"jobs": list(jobs.values()), "total": total, "pages": pages}

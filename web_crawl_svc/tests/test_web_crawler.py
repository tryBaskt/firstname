from hashlib import sha256
from typing import Any
from urllib.error import HTTPError

import pytest

from web_crawl_svc.services.crawl_and_parse import (
    CrawlMessageError,
    CrawlRequest,
    PageNotFoundError,
    fetch_html_page,
)


def test_fetch_treats_http_404_as_non_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    class NotFoundOpener:
        def open(self, *_: Any, **__: Any) -> Any:
            raise HTTPError(
                "https://example.com/missing",
                404,
                "Not Found",
                hdrs=None,
                fp=None,
            )

    monkeypatch.setattr(
        "web_crawl_svc.services.crawl_and_parse.validate_public_url",
        lambda _: None,
    )
    monkeypatch.setattr(
        "web_crawl_svc.services.crawl_and_parse.build_opener",
        lambda *_: NotFoundOpener(),
    )

    with pytest.raises(PageNotFoundError):
        fetch_html_page(
            "https://example.com/missing",
            "firstname-crawler/1.0",
            10,
            1_000_000,
        )


def test_crawler_rejects_a_mismatched_url_hash() -> None:
    message = _message()
    message["payload"]["canonical_url_hash"] = "0" * 64

    with pytest.raises(CrawlMessageError):
        CrawlRequest.from_message(message)


def _message(url: str = "https://example.com/") -> dict[str, Any]:
    return {
        "action": "crawl_url",
        "payload": {
            "site_id": "site-1",
            "crawl_run_id": "crawl-1",
            "root_url": url,
            "url": url,
            "canonical_url_hash": sha256(url.encode()).hexdigest(),
            "depth": 0,
        },
    }

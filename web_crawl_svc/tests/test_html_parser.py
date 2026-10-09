import pytest

from web_crawl_svc.services.crawl_and_parse import (
    _parse_document,
    discover_sitemap_urls,
    extract_child_urls,
    is_url_within_root,
)


def test_link_discovery_stops_at_the_configured_depth() -> None:
    document = _parse_document(
        b"<html><body><a href='/child'>Child</a></body></html>",
        "https://example.com/",
    )

    assert (
        extract_child_urls(
            document,
            root_url="https://example.com/",
            final_url="https://example.com/",
            depth=3,
            max_depth=3,
            max_links=10,
        )
        == []
    )


def test_link_discovery_only_returns_urls_below_root_path() -> None:
    document = _parse_document(
        b"""
        <html><body>
        <a href='/docs/guide'>Guide</a>
        <a href='/docs/api/users'>API</a>
        <a href='/docs-old/archive'>Similar prefix</a>
        <a href='/pricing'>Pricing</a>
        </body></html>
        """,
        "https://example.com/docs/",
    )

    assert extract_child_urls(
        document,
        root_url="https://example.com/docs/",
        final_url="https://example.com/docs/",
        depth=0,
        max_depth=3,
        max_links=10,
    ) == [
        "https://example.com/docs/api/users",
        "https://example.com/docs/guide",
    ]


def test_sitemap_discovery_follows_indexes_and_filters_to_root_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources: dict[str, bytes | str | None] = {
        "https://example.com/robots.txt": "Sitemap: /sitemap-index.xml\n",
        "https://example.com/sitemap.xml": None,
        "https://example.com/sitemap-index.xml": b"""
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <sitemap><loc>https://example.com/docs-sitemap.xml</loc></sitemap>
            </sitemapindex>
        """,
        "https://example.com/docs-sitemap.xml": b"""
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/docs/guide</loc></url>
              <url><loc>https://example.com/pricing</loc></url>
              <url><loc>https://outside.example/docs/other</loc></url>
            </urlset>
        """,
    }
    monkeypatch.setattr(
        "web_crawl_svc.services.crawl_and_parse._fetch_discovery_resource",
        lambda url, **_: resources.get(url),
    )

    assert discover_sitemap_urls("https://example.com/docs/", 10) == [
        "https://example.com/docs/guide"
    ]


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("https://example.com/docs/guide", True),
        ("https://example.com/docs/api/users", True),
        ("https://example.com/docs", False),
        ("https://example.com/docs-old", False),
        ("https://example.com/pricing", False),
        ("https://other.example/docs/guide", False),
    ],
)
def test_url_hierarchy(candidate: str, expected: bool) -> None:
    assert is_url_within_root(candidate, "https://example.com/docs/") is expected

"""Crawl lifecycle and token-fenced discovery locking."""

import contextlib
from typing import Any

from boto3.dynamodb.conditions import Attr

from web_crawl_svc.clients.dynamodb import DynamoDBClient, DynamoDBConditionNotMetError


class CrawlRunsTable:
    def __init__(self, dynamodb: DynamoDBClient) -> None:
        self.dynamodb = dynamodb

    @staticmethod
    def new_item(*, site_id: str, crawl_run_id: str, created_at: str, root_url: str) -> dict:
        return dict(
            site_id=site_id,
            crawl_run_id=crawl_run_id,
            created_at=created_at,
            updated_at=created_at,
            root_url=root_url,
            status="PENDING",
        )

    def get(self, *, site_id: str, crawl_run_id: str) -> dict[str, Any] | None:
        return self.dynamodb.get_item(
            key=dict(site_id=site_id, crawl_run_id=crawl_run_id), consistent_read=True
        )

    def list_active(self) -> list[dict[str, Any]]:
        return self.dynamodb.scan(
            Attr("status").is_in(["PENDING", "CRAWLING_AND_PARSING"]),
            ConsistentRead=True,
        )

    def acquire_discovery_lock(
        self, *, site_id: str, crawl_run_id: str, token: str, now: str, expires_at: str
    ) -> bool:
        try:
            self.dynamodb.update_item(
                key=dict(site_id=site_id, crawl_run_id=crawl_run_id),
                update_expression=(
                    "SET discovery_lock_token = :token, discovery_lock_expires_at = :expiry"
                ),
                expression_attribute_values={":token": token, ":expiry": expires_at},
                condition_expression=(
                    Attr("status").is_in(["PENDING", "CRAWLING_AND_PARSING"])
                    & (
                        Attr("discovery_lock_expires_at").not_exists()
                        | Attr("discovery_lock_expires_at").lte(now)
                    )
                ),
            )
        except DynamoDBConditionNotMetError:
            return False
        return True

    def release_discovery_lock(self, *, site_id: str, crawl_run_id: str, token: str) -> None:
        with contextlib.suppress(DynamoDBConditionNotMetError):
            self.dynamodb.update_item(
                key=dict(site_id=site_id, crawl_run_id=crawl_run_id),
                update_expression="REMOVE discovery_lock_token, discovery_lock_expires_at",
                condition_expression=Attr("discovery_lock_token").eq(token),
            )

    def complete_crawl(
        self,
        *,
        site_id: str,
        crawl_run_id: str,
        token: str,
        updated_at: str,
        completed_page_count: int,
        failed_page_count: int,
    ) -> bool:
        """Finish a crawl under the same lock used to register child pages.

        The caller checks that every page is terminal. A partially successful
        crawl records incomplete=True; all-failed crawls are FAILED. Completion
        describes crawling only, not job ingestion or deduplication.
        """
        try:
            self.dynamodb.update_item(
                key=dict(site_id=site_id, crawl_run_id=crawl_run_id),
                update_expression=(
                    "SET #s = :status, updated_at = :now, completed_at = :now, "
                    "completed_page_count = :completed, failed_page_count = :failed, "
                    "incomplete = :incomplete "
                    "REMOVE discovery_lock_token, discovery_lock_expires_at"
                ),
                expression_attribute_names={"#s": "status"},
                expression_attribute_values={
                    ":status": "COMPLETED" if completed_page_count else "FAILED",
                    ":now": updated_at,
                    ":completed": completed_page_count,
                    ":failed": failed_page_count,
                    ":incomplete": failed_page_count > 0,
                },
                condition_expression=(
                    Attr("status").is_in(["PENDING", "CRAWLING_AND_PARSING"])
                    & Attr("discovery_lock_token").eq(token)
                    & Attr("discovery_lock_expires_at").gt(updated_at)
                ),
            )
        except DynamoDBConditionNotMetError:
            return False
        return True

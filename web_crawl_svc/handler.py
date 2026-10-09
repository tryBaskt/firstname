"""SQS Lambda entry point for crawling queued website pages."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

import boto3

from web_crawl_svc.clients.dynamodb import DynamoDBClient
from web_crawl_svc.clients.s3 import S3Client
from web_crawl_svc.clients.sqs import SQSClient
from web_crawl_svc.services.crawl_and_parse import CrawlAndParseService, CrawlMessageError
from web_crawl_svc.tables.crawl_pages import CrawlPagesTable
from web_crawl_svc.tables.crawl_runs import CrawlRunsTable
from web_crawl_svc.tables.sites import SitesTable
from web_crawl_svc.tables.job_posts import JobPostsTable
from web_crawl_svc.tables.job_boards import JobBoardsTable
from web_crawl_svc.tables.job_post_job_boards import JobPostJobBoardsTable


@lru_cache
def _web_crawler_service() -> CrawlAndParseService:
    region = _required_env("AWS_REGION")
    session = boto3.Session(region_name=region)
    dynamodb = session.resource("dynamodb", region_name=region)

    sqs = session.client("sqs", region_name=region)
    return CrawlAndParseService(
        job_boards=JobBoardsTable(
            DynamoDBClient(table=dynamodb.Table(_required_env("JOB_BOARDS_TABLE")))
        ),
        job_post_job_boards=JobPostJobBoardsTable(
            DynamoDBClient(table=dynamodb.Table(_required_env("JOB_POST_JOB_BOARDS_TABLE")))
        ),
        job_posts=JobPostsTable(
            DynamoDBClient(table=dynamodb.Table(_required_env("JOB_POSTS_TABLE")))
        ),
        crawl_pages=CrawlPagesTable(
            DynamoDBClient(table=dynamodb.Table(_required_env("CRAWL_PAGES_TABLE")))
        ),
        crawl_runs=CrawlRunsTable(
            DynamoDBClient(table=dynamodb.Table(_required_env("CRAWL_RUNS_TABLE")))
        ),
        sites=SitesTable(DynamoDBClient(table=dynamodb.Table(_required_env("SITES_TABLE")))),
        s3=S3Client(
            client=session.client("s3", region_name=region),
            bucket_name=_required_env("APPLICATION_S3_BUCKET"),
        ),
        crawl_queue=SQSClient(sqs, _required_env("CRAWL_QUEUE_URL")),
        retry_delay_seconds=int(_required_env("CRAWLER_RETRY_DELAY_SECONDS")),
        lease_seconds=int(_required_env("CRAWLER_LEASE_SECONDS")),
        parser_version=_required_env("PARSER_VERSION"),
        max_depth=int(_required_env("CRAWLER_MAX_DEPTH")),
        max_links_per_page=_optional_limit("CRAWLER_MAX_LINKS_PER_PAGE"),
        max_discovered_pages=_optional_limit("CRAWLER_MAX_DISCOVERED_PAGES"),
        user_agent=_required_env("CRAWLER_USER_AGENT"),
        request_timeout_seconds=float(_required_env("CRAWLER_REQUEST_TIMEOUT_SECONDS")),
        max_response_bytes=int(_required_env("CRAWLER_MAX_RESPONSE_BYTES")),
        max_attempts=int(_required_env("CRAWLER_MAX_ATTEMPTS")),
    )


def _process_record(record: dict[str, Any]) -> None:
    try:
        message = json.loads(record.get("body", "{}"))
    except json.JSONDecodeError as error:
        raise CrawlMessageError("Queued crawl message must be valid JSON") from error
    if not isinstance(message, dict):
        raise CrawlMessageError("Queued crawl message must be an object")

    if record.get("eventSourceARN") == _required_env("CRAWL_DLQ_ARN"):
        _web_crawler_service().process_dead_letter(message)
    else:
        _web_crawler_service().process_message(message)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process SQS records and return failures for individual retries."""
    if event.get("action") == "warmup":
        _web_crawler_service()
        return {"warmed": True}

    if event.get("action") == "recover_crawls":
        _web_crawler_service().recover(remaining_ms=context.get_remaining_time_in_millis)
        return {"recovered": True}

    batch_item_failures = []
    for record in event.get("Records", []):
        try:
            _process_record(record)
        except Exception as error:
            print(
                json.dumps(
                    {
                        "level": "error",
                        "message": "Failed processing crawl message",
                        "message_id": record.get("messageId", ""),
                        "error": str(error),
                    }
                )
            )
            try:
                region = _required_env("AWS_REGION")
                boto3.Session(region_name=region).client("sqs").change_message_visibility(
                    QueueUrl=_required_env(
                        "CRAWL_DLQ_URL"
                        if record.get("eventSourceARN") == _required_env("CRAWL_DLQ_ARN")
                        else "CRAWL_QUEUE_URL"
                    ),
                    ReceiptHandle=record["receiptHandle"],
                    VisibilityTimeout=int(_required_env("CRAWLER_RETRY_DELAY_SECONDS")),
                )
            except Exception as visibility_error:
                print(
                    json.dumps(
                        {
                            "message": "Retry visibility update failed",
                            "error": str(visibility_error),
                        }
                    )
                )
            batch_item_failures.append({"itemIdentifier": record.get("messageId", "")})

    return {"batchItemFailures": batch_item_failures}


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_limit(name: str) -> int | None:
    """An omitted limit means unlimited; explicit limits must be positive."""
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    limit = int(value)
    if limit <= 0:
        raise ValueError(f"{name} must be positive when configured")
    return limit

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ClientError

from web_crawl_svc.clients.dynamodb import (
    DynamoDBClient,
    DynamoDBClientError,
    DynamoDBConditionNotMetError,
    to_dynamodb_value,
)
from web_crawl_svc.tables.crawl_pages import CrawlPagesTable


def test_failure_transaction_serializes_page_and_run_updates_without_network() -> None:
    resource = boto3.resource(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    requests = []

    def capture(params: dict[str, Any], **_: Any) -> tuple[Any, dict]:
        requests.append(json.loads(params["body"]))
        return AWSResponse("https://example.com", 200, {}, None), {}

    resource.meta.client.meta.events.register("before-call.dynamodb.TransactWriteItems", capture)
    table = CrawlPagesTable(DynamoDBClient(resource.Table("pages")))
    assert table.claim(
        {
            "site_id": "site",
            "crawl_run_id": "run",
            "canonical_url_hash": "hash",
            "attempt_count": 0,
        },
        runs_table_name="runs",
        token="worker",
        now="now",
        expires_at="later",
        max_attempts=2,
    )
    run, page = [item["Update"] for item in requests[0]["TransactItems"]]
    assert page["TableName"] == "pages"
    assert page["Key"]["crawl_run_id"] == {"S": "run"}
    assert page["ExpressionAttributeValues"][":next"] == {"N": "1"}
    assert page["ExpressionAttributeValues"][":token"] == {"S": "worker"}
    assert run["TableName"] == "runs"
    assert "pending_page_count" not in run["UpdateExpression"]


@pytest.mark.parametrize(
    "codes,expected",
    [
        (["ConditionalCheckFailed", "None"], DynamoDBConditionNotMetError),
        (["None", "ConditionalCheckFailed"], DynamoDBConditionNotMetError),
        (["TransactionConflict", "None"], DynamoDBClientError),
        (["ConditionalCheckFailed", "ProvisionedThroughputExceeded"], DynamoDBClientError),
    ],
)
def test_transaction_conflicts_are_retried_not_treated_as_duplicates(codes, expected) -> None:
    from types import SimpleNamespace

    def fail(**_: Any) -> None:
        raise ClientError(
            {
                "Error": {"Code": "TransactionCanceledException"},
                "CancellationReasons": [{"Code": code} for code in codes],
            },
            "TransactWriteItems",
        )

    table = SimpleNamespace(meta=SimpleNamespace(client=SimpleNamespace(transact_write_items=fail)))
    with pytest.raises(expected) as error:
        DynamoDBClient(table).transact_write([])
    assert type(error.value) is expected


class Status(Enum):
    queued = "queued"


@dataclass
class CrawlRun:
    crawl_run_id: str
    status: Status
    score: float
    started_at: datetime


class FakeTable:
    def __init__(self) -> None:
        self.item: dict[str, Any] | None = None

    def put_item(self, *, Item: dict[str, Any]) -> None:
        self.item = Item


def test_to_dynamodb_value_converts_dataclass_domain_values() -> None:
    item = to_dynamodb_value(
        CrawlRun(
            crawl_run_id="run-1",
            status=Status.queued,
            score=1.25,
            started_at=datetime(2026, 9, 27, tzinfo=UTC),
        )
    )

    assert item == {
        "crawl_run_id": "run-1",
        "status": "QUEUED",
        "score": Decimal("1.25"),
        "started_at": "2026-09-27T00:00:00+00:00",
    }


def test_put_item_converts_values_before_writing() -> None:
    table = FakeTable()

    DynamoDBClient(table).put_item({"score": 1.25})

    assert table.item == {"score": Decimal("1.25")}


def test_put_item_wraps_client_errors() -> None:
    class FailingTable:
        def put_item(self, **_: Any) -> None:
            raise RuntimeError("unavailable")

    with pytest.raises(DynamoDBClientError) as error:
        DynamoDBClient(FailingTable()).put_item({"site_id": "site-1"})

    assert error.value.code == "DYNAMODB_PUT_ITEM_FAILED"


def test_update_item_identifies_a_failed_condition() -> None:
    class StaleWriteTable:
        def update_item(self, **_: Any) -> None:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "The conditional request failed",
                    }
                },
                "UpdateItem",
            )

    with pytest.raises(DynamoDBConditionNotMetError) as error:
        DynamoDBClient(StaleWriteTable()).update_item(
            key={"site_id": "site-1"},
            update_expression="SET modified_at = :modified_at",
        )

    assert error.value.code == "DYNAMODB_CONDITION_NOT_MET"


def test_query_loads_every_page_when_no_limit_is_set() -> None:
    class PaginatedTable:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def query(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": [{"id": "1"}], "LastEvaluatedKey": {"id": "1"}}
            return {"Items": [{"id": "2"}]}

    table = PaginatedTable()

    items = DynamoDBClient(table).query("condition")

    assert items == [{"id": "1"}, {"id": "2"}]
    assert table.calls[1]["ExclusiveStartKey"] == {"id": "1"}

def test_query_honors_an_explicit_limit_without_pagination() -> None:
    class LimitedTable:
        def __init__(self) -> None:
            self.calls = 0

        def query(self, **_: Any) -> dict[str, Any]:
            self.calls += 1
            return {"Items": [{"id": "1"}], "LastEvaluatedKey": {"id": "1"}}

    table = LimitedTable()

    items = DynamoDBClient(table).query("condition", Limit=1)

    assert items == [{"id": "1"}]
    assert table.calls == 1


def test_scan_loads_every_page() -> None:
    class PaginatedTable:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def scan(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": [{"id": "1"}], "LastEvaluatedKey": {"id": "1"}}
            return {"Items": [{"id": "2"}]}

    table = PaginatedTable()

    items = DynamoDBClient(table).scan()

    assert items == [{"id": "1"}, {"id": "2"}]
    assert table.calls[1]["ExclusiveStartKey"] == {"id": "1"}

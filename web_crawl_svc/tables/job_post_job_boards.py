"""Atomic job-board relationships and exactly-once unique-job counting."""

from web_crawl_svc.clients.dynamodb import DynamoDBClient, DynamoDBConditionNotMetError
from boto3.dynamodb.conditions import Attr


class JobPostJobBoardsTable:
    def __init__(self, dynamodb: DynamoDBClient) -> None:
        self.dynamodb = dynamodb

    def link_if_absent(
        self, *, job_board_id: str, job_post_id: str, job_boards_table_name: str,
        url: str, crawl_run_id: str, created_at: str, discovered_from_url: str | None = None
    ) -> bool:
        key = {"job_board_id": job_board_id, "job_post_id": job_post_id}
        item = {**key, "url": url, "crawl_run_id": crawl_run_id,
                "created_at": created_at, "updated_at": created_at,
                "last_seen_at": created_at, "last_seen_crawl_run_id": crawl_run_id}
        if discovered_from_url:
            item["discovered_from_url"] = discovered_from_url
        try:
            self.dynamodb.transact_write([
                {"Put": {
                    "TableName": self.dynamodb.table_name,
                    "Item": item,
                    "ConditionExpression": "attribute_not_exists(job_board_id)",
                }},
                {"Update": {
                    "TableName": job_boards_table_name,
                    "Key": {"job_board_id": job_board_id},
                    "UpdateExpression": "ADD num_job_posts :one",
                    "ExpressionAttributeValues": {":one": 1},
                    "ConditionExpression": "attribute_exists(job_board_id)",
                }},
            ])
        except DynamoDBConditionNotMetError:
            # Only an existing relationship is a duplicate; a missing board is an error.
            if self.dynamodb.get_item(key=key, consistent_read=True) is None:
                raise
            self.dynamodb.update_item(
                key=key,
                update_expression=(
                    "SET last_seen_at = :now, last_seen_crawl_run_id = :run, updated_at = :now"
                ),
                expression_attribute_values={":now": created_at, ":run": crawl_run_id},
                condition_expression=Attr("job_board_id").exists(),
            )
            return False
        return True

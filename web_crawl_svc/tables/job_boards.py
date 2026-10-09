"""Source job-board metadata and unique-job counts."""

from web_crawl_svc.clients.dynamodb import DynamoDBClient


class JobBoardsTable:
    def __init__(self, dynamodb: DynamoDBClient) -> None:
        self.dynamodb = dynamodb

    def upsert(self, *, job_board_id: str, name: str, url: str, updated_at: str) -> None:
        self.dynamodb.update_item(
            key={"job_board_id": job_board_id},
            update_expression=(
                "SET job_board_name = :name, job_board_url = :url, updated_at = :now, "
                "created_at = if_not_exists(created_at, :now), "
                "num_job_posts = if_not_exists(num_job_posts, :zero)"
            ),
            expression_attribute_values={":name": name, ":url": url, ":now": updated_at, ":zero": 0},
        )

from unittest.mock import Mock

import pytest

from web_crawl_svc.clients.dynamodb import DynamoDBClientError, DynamoDBConditionNotMetError
from web_crawl_svc.tables.job_boards import JobBoardsTable
from web_crawl_svc.tables.job_post_job_boards import JobPostJobBoardsTable


def link(table, board="a16z"):
    return table.link_if_absent(job_board_id=board, job_post_id="job", job_boards_table_name="boards",
                                url="https://jobs.ashbyhq.com/company/job", crawl_run_id="run",
                                created_at="now")


def test_metadata_update_preserves_existing_count_and_created_at():
    client = Mock()
    JobBoardsTable(client).upsert(job_board_id="a16z", name="a16z", url="https://jobs.a16z.com/",
                                 updated_at="now")
    expression = client.update_item.call_args.kwargs["update_expression"]
    assert "num_job_posts = if_not_exists(num_job_posts, :zero)" in expression
    assert "created_at = if_not_exists(created_at, :now)" in expression


def test_new_relationship_and_increment_are_one_transaction():
    client = Mock()
    client.table_name = "links"
    assert link(JobPostJobBoardsTable(client))
    put, increment = client.transact_write.call_args.args[0]
    assert put["Put"]["Item"]["job_board_id"] == "a16z"
    assert put["Put"]["ConditionExpression"] == "attribute_not_exists(job_board_id)"
    assert increment["Update"]["UpdateExpression"] == "ADD num_job_posts :one"


def test_duplicate_refreshes_last_seen_without_separate_count_increment():
    client = Mock()
    client.transact_write.side_effect = DynamoDBConditionNotMetError("duplicate")
    client.get_item.return_value = {"job_post_id": "job"}
    assert not link(JobPostJobBoardsTable(client))
    update = client.update_item.call_args.kwargs
    assert "last_seen_at" in update["update_expression"]
    assert "num_job_posts" not in update["update_expression"]


def test_missing_board_is_not_silently_treated_as_duplicate():
    client = Mock()
    client.transact_write.side_effect = DynamoDBConditionNotMetError("board missing")
    client.get_item.return_value = None
    with pytest.raises(DynamoDBConditionNotMetError):
        link(JobPostJobBoardsTable(client))


def test_transaction_failure_retries():
    client = Mock()
    client.transact_write.side_effect = DynamoDBClientError("unavailable")
    with pytest.raises(DynamoDBClientError):
        link(JobPostJobBoardsTable(client))


def test_same_job_can_belong_to_two_boards():
    client = Mock()
    table = JobPostJobBoardsTable(client)
    assert link(table, "a16z")
    assert link(table, "thrive")
    puts = [call.args[0][0]["Put"]["Item"] for call in client.transact_write.call_args_list]
    assert puts[0]["job_post_id"] == puts[1]["job_post_id"]
    assert puts[0]["job_board_id"] != puts[1]["job_board_id"]

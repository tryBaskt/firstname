from unittest.mock import Mock

import pytest

from web_crawl_svc.clients.dynamodb import DynamoDBClientError, DynamoDBConditionNotMetError
from web_crawl_svc.tables.job_posts import JobPostsTable


def job():
    return dict(ats_provider="ashby", employer_slug="openai", employer_job_id="job-id",
                title="Engineer", description="Build software", location="New York")


def insert(table, record=None, url="https://jobs.ashbyhq.com/openai/job-id"):
    return table.create_if_absent(job=record or job(), url=url,
                                 parsed_json_s3_key="parsed/job.json", created_at="now")


def test_insert_uses_conditional_canonical_id():
    client = Mock()
    assert insert(JobPostsTable(client))
    item = client.put_item.call_args.args[0]
    assert item["employer_job_id"] == "job-id"
    assert item["description"] == "Build software"
    assert client.put_item.call_args.kwargs["condition_expression"] is not None


def test_duplicate_does_not_overwrite():
    client = Mock()
    client.put_item.side_effect = DynamoDBConditionNotMetError("already exists")
    assert not insert(JobPostsTable(client))
    client.update_item.assert_not_called()


def test_storage_failure_propagates_for_retry():
    client = Mock()
    client.put_item.side_effect = DynamoDBClientError("unavailable")
    with pytest.raises(DynamoDBClientError):
        insert(JobPostsTable(client))


def test_source_url_does_not_change_job_identity():
    client = Mock()
    table = JobPostsTable(client)
    insert(table, url="https://jobs.ashbyhq.com/openai/job-id?source=thrive")
    insert(table, url="https://jobs.ashbyhq.com/openai/job-id?source=a16z")
    items = [call.args[0] for call in client.put_item.call_args_list]
    assert items[0]["job_post_id"] == items[1]["job_post_id"]
    assert table.job_post_id(ats_provider="ashby", employer_slug="other", employer_job_id="job-id") \
        != items[0]["job_post_id"]


def test_large_description_uses_saved_json_reference():
    client = Mock()
    insert(JobPostsTable(client), {**job(), "description": "x" * 200001})
    item = client.put_item.call_args.args[0]
    assert "description" not in item
    assert item["description_s3_key"] == "parsed/job.json"

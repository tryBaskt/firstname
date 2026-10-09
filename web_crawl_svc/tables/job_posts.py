"""Insert-only canonical job records keyed by employer ATS identity."""

import json
from hashlib import sha256

from boto3.dynamodb.conditions import Attr

from web_crawl_svc.clients.dynamodb import DynamoDBClient, DynamoDBConditionNotMetError


class JobPostsTable:
    def __init__(self, dynamodb: DynamoDBClient) -> None:
        self.dynamodb = dynamodb

    @staticmethod
    def job_post_id(*, ats_provider: str, employer_slug: str, employer_job_id: str) -> str:
        identity = json.dumps([ats_provider, employer_slug, employer_job_id], separators=(",", ":"))
        return sha256(identity.encode()).hexdigest()

    def create_if_absent(
        self, *, job: dict, url: str, parsed_json_s3_key: str, created_at: str
    ) -> bool:
        """Return True on insertion, False if the job already exists.

        The condition handles concurrent workers without a read-before-write race.
        Full API data and oversized descriptions remain available in parsed S3 JSON.
        """
        identity = {name: job[name] for name in ("ats_provider", "employer_slug", "employer_job_id")}
        if any(not isinstance(value, str) or not value for value in identity.values()):
            raise ValueError("Verified job must contain its complete ATS identity")
        item = {
            "job_post_id": self.job_post_id(**identity),
            **identity,
            "title": job["title"],
            "url": url,
            "live": True,
            "created_at": created_at,
            "updated_at": created_at,
            "parsed_json_s3_key": parsed_json_s3_key,
        }
        location = job.get("location")
        if isinstance(location, dict):
            location = location.get("name")
        if isinstance(location, str) and len(location.encode()) <= 10_000:
            item["location"] = location
        description = job["description"]
        if len(description.encode()) <= 200_000:
            item["description"] = description
        else:
            item["description_s3_key"] = parsed_json_s3_key
        if len(json.dumps(item, ensure_ascii=False).encode()) > 390_000:
            raise ValueError("Job record is too large for DynamoDB")
        try:
            self.dynamodb.put_item(item, condition_expression=Attr("job_post_id").not_exists())
        except DynamoDBConditionNotMetError:
            return False
        return True

from typing import Any

from boto3.dynamodb.conditions import Attr

from web_crawl_svc.clients.dynamodb import DynamoDBClient, DynamoDBConditionNotMetError


class SitesTable:
    """Persistence operations for website records."""

    def __init__(self, dynamodb: DynamoDBClient) -> None:
        """Bind the environment's Sites client during API or worker setup.

        Construction performs no reads or writes. Each site is keyed by site_id.
        """
        self.dynamodb = dynamodb

    def crawl_setup_write(
        self, *, site_id: str, root_url: str, crawl_run_id: str, timestamp: str
    ) -> dict[str, Any]:
        """Build the site transaction operation for a manual or nightly crawl.

        CrawlSetupService passes this operation to CrawlPagesTable.initialize_run
        to commit with the new run and root page. This method itself performs no
        database write. root_url must already be normalized by the caller.

        Sets latest_crawl_run_id and updated_at on every setup, but initializes
        last_crawl_run_id and created_at only if absent. Existing content-change
        fields are left unchanged. timestamp is the crawl's
        creation time; the returned operation can create a missing site record.
        """
        return {
            "Update": {
                "TableName": self.dynamodb.table_name,
                "Key": {"site_id": site_id},
                "UpdateExpression": (
                    "SET root_url = :url, normalized_root_url = :url, "
                    "last_crawl_run_id = if_not_exists(last_crawl_run_id, :run), "
                    "latest_crawl_run_id = :run, updated_at = :now, "
                    "created_at = if_not_exists(created_at, :now)"
                ),
                "ExpressionAttributeValues": {
                    ":url": root_url,
                    ":run": crawl_run_id,
                    ":now": timestamp,
                },
            }
        }

    def mark_content_changed(
        self,
        *,
        site_id: str,
        crawl_run_id: str,
        modified_at: str,
    ) -> None:
        """Advance the site's content-change timestamp and associated crawl ID.

        The crawler calls this when a root or child page's raw content changes,
        including when replaying a saved checkpoint after an interrupted attempt.
        modified_at is the page's crawl timestamp. Only a missing or strictly older
        stored timestamp is replaced, so older/equal updates are harmless no-ops.

        Does not change latest_crawl_run_id or updated_at. Other database errors
        propagate. The condition does not require an existing site record.
        """
        try:
            self.dynamodb.update_item(
                key={"site_id": site_id},
                update_expression=(
                    "SET last_crawl_run_id = :crawl_run_id, modified_at = :modified_at"
                ),
                expression_attribute_values={
                    ":crawl_run_id": crawl_run_id,
                    ":modified_at": modified_at,
                },
                condition_expression=(
                    Attr("modified_at").not_exists() | Attr("modified_at").lt(modified_at)
                ),
            )
        except DynamoDBConditionNotMetError:
            return

    def get(self, site_id: str) -> dict[str, Any] | None:
        """Read one site strongly consistently, returning None if it is absent.

        Used by internal workers to retrieve the site's URL, crawl pointers,
        and timestamps.
        """
        return self.dynamodb.get_item(key={"site_id": site_id}, consistent_read=True)

    def list_all(self) -> list[dict[str, Any]]:
        """Return all site records using the client's paginated table scan.

        Nightly refresh uses this list to prepare a crawl for each registered site.
        No user or status filter is applied; reads use default eventual consistency.
        """
        return self.dynamodb.scan()

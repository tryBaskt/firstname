import contextlib
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from web_crawl_svc.clients.dynamodb import DynamoDBClient, DynamoDBConditionNotMetError

TERMINAL_PAGE_STATUSES = {"COMPLETED", "FAILED"}
LATEST_PAGE_INDEX = "site_url_crawled_at_index"
MAX_CHILDREN_ATTEMPTS = 2


class CrawlPagesTable:
    """Page state and fenced writes. No persisted aggregate counters."""

    def __init__(self, dynamodb: DynamoDBClient) -> None:
        """Bind the CrawlPages client during API or Lambda dependency setup.

        The client targets the environment's page table. Construction performs
        no reads or writes.
        """
        self.dynamodb = dynamodb

    @staticmethod
    def new_item(
        *,
        crawl_run_id: str,
        canonical_url_hash: str,
        site_id: str,
        url: str,
        root_url: str,
        depth: int,
        created_at: str,
        parent_url: str | None = None,
    ) -> dict[str, Any]:
        """Build an unsaved QUEUED page with zero attempts and pending dispatch.

        CrawlSetupService uses this for the root; child registration uses it for
        discovered URLs. The caller supplies the canonical URL and its hash,
        crawl/site IDs, depth, scope URL, and creation timestamp. parent_url is
        omitted for the root. Also builds site_url_key for cross-run lookups.
        The returned record must be persisted before it is sent to SQS.
        """
        item = dict(
            crawl_run_id=crawl_run_id,
            canonical_url_hash=canonical_url_hash,
            site_id=site_id,
            site_url_key=f"{site_id}#{canonical_url_hash}",
            url=url,
            root_url=root_url,
            depth=depth,
            status="QUEUED",
            created_at=created_at,
            updated_at=created_at,
            attempt_count=0,
            children_attempt_count=0,
            dispatch_pending=True,
        )
        if parent_url is not None:
            item["parent_url"] = parent_url
        return item

    @staticmethod
    def key(page: dict) -> dict:
        """Extract the complete primary key from a page record without database I/O.

        Worker mutations and checkpoint reads use crawl_run_id as the partition
        key and canonical_url_hash as the sort key. Missing fields raise KeyError.
        """
        return {k: page[k] for k in ("crawl_run_id", "canonical_url_hash")}

    def initialize_run(self, run: dict, root: dict, runs_table_name: str, site_write: dict) -> None:
        """Atomically commit the site update, new run, and queued root page.

        Called by CrawlSetupService before manual or nightly SQS dispatch. run
        and root are prepared records; runs_table_name identifies CrawlRuns;
        site_write is the prepared Sites transaction operation.

        Both puts require their exact keyed items to be absent. If either exists,
        DynamoDBConditionNotMetError is raised and the site update is also rolled
        back. This transaction does not send messages or create S3 objects.
        """
        self.dynamodb.transact_write(
            [
                site_write,
                {
                    "Put": {
                        "TableName": runs_table_name,
                        "Item": run,
                        "ConditionExpression": "attribute_not_exists(crawl_run_id)",
                    }
                },
                {
                    "Put": {
                        "TableName": self.dynamodb.table_name,
                        "Item": root,
                        "ConditionExpression": "attribute_not_exists(crawl_run_id)",
                    }
                },
            ]
        )

    def get(self, *, crawl_run_id: str, canonical_url_hash: str) -> dict[str, Any] | None:
        """Read a single page strongly consistently, or return None if absent.

        Workers use this before claiming work and after claims/checkpoints to
        inspect the current owner, attempt count, and saved processing state.
        """
        return self.dynamodb.get_item(
            key=dict(crawl_run_id=crawl_run_id, canonical_url_hash=canonical_url_hash),
            consistent_read=True,
        )

    def list_for_run(self, crawl_run_id: str) -> list[dict[str, Any]]:
        """Return all pages for a run using paginated, strongly consistent queries.

        Used for discovery deduplication/capacity checks, crawl completion,
        API progress counts, recovery, and generator input loading. No status
        filter is applied. Multiple query pages are not a transactional snapshot;
        discovery and readiness callers coordinate using the run discovery lock.
        """
        return self.dynamodb.query(Key("crawl_run_id").eq(crawl_run_id), ConsistentRead=True)

    def get_latest_crawled(self, *, site_id: str, canonical_url_hash: str) -> dict | None:
        """Return the newest indexed crawl record for this site's canonical URL.

        The crawler uses its raw_html_hash to detect changes before saving the
        current raw checkpoint. Query site_url_crawled_at_index by site_url_key,
        sorting crawled_at descending and reading one record across all runs.

        Returns None if no record is indexed. This GSI is eventually consistent
        and excludes records without its keys; COMPLETED status is not required,
        and the current run is not explicitly excluded.
        """
        pages = self.dynamodb.query(
            Key("site_url_key").eq(f"{site_id}#{canonical_url_hash}"),
            IndexName=LATEST_PAGE_INDEX,
            ScanIndexForward=False,
            Limit=1,
        )
        return pages[0] if pages else None

    def counts_for_run(self, crawl_run_id: str) -> dict[str, int]:
        """Calculate API progress counts from the run's current page records.

        Internal callers use this instead of persisted counters.
        Discovered is the total; completed and failed are separate terminal counts;
        pending is the remainder. Reads pages but does not update the run or pages.
        """
        pages = self.list_for_run(crawl_run_id)
        complete = sum(p["status"] == "COMPLETED" for p in pages)
        failed = sum(p["status"] == "FAILED" for p in pages)
        return dict(
            discovered_page_count=len(pages),
            completed_page_count=complete,
            failed_page_count=failed,
            pending_page_count=len(pages) - complete - failed,
        )

    def claim(
        self,
        page: dict,
        *,
        runs_table_name: str,
        token: str,
        now: str,
        expires_at: str,
        max_attempts: int,
    ) -> bool:
        """Atomically claim an eligible page and mark its run as crawling/parsing.

        process_message calls this before fetching or resuming a checkpoint.
        page supplies the expected key, site, attempt count, and checkpoint state;
        token identifies this worker, and now/expires_at define its UTC ISO lease.
        runs_table_name identifies the run table in the cross-table transaction.

        Requires a pending/crawling run and a queued page or expired working page,
        an unchanged attempt count, and no outstanding retry delay. Sets the page
        to CRAWLING_AND_PARSING and clears its pending-dispatch flag. Attempts
        increase only without a parsed checkpoint; checkpoint resumption uses
        children_attempt_count with a separate two-attempt limit. The first
        children attempt after parsing is counted by start_children_attempt.
        Returns False for exhausted attempts or a
        rejected condition; other database errors propagate.
        """
        attempts = int(page.get("attempt_count", 0))
        children_attempts = int(page.get("children_attempt_count", 0))
        parsed = bool(page.get("parsed_content_s3_key"))
        checkpoint_condition = (
            "parsed_content_s3_key = :parsed_key"
            if parsed
            else "attribute_not_exists(parsed_content_s3_key)"
        )
        if self.attempts_exhausted(page, max_attempts):
            return False
        children_condition = "children_attempt_count = :previous_children"
        if children_attempts == 0:
            children_condition = (
                f"({children_condition} OR attribute_not_exists(children_attempt_count))"
            )
        try:
            self.dynamodb.transact_write(
                [
                    {
                        "Update": {
                            "TableName": runs_table_name,
                            "Key": {
                                "site_id": page["site_id"],
                                "crawl_run_id": page["crawl_run_id"],
                            },
                            "UpdateExpression": "SET #s = :working, updated_at = :now",
                            "ConditionExpression": "#s IN (:pending, :working)",
                            "ExpressionAttributeNames": {"#s": "status"},
                            "ExpressionAttributeValues": {
                                ":pending": "PENDING",
                                ":working": "CRAWLING_AND_PARSING",
                                ":now": now,
                            },
                        }
                    },
                    {
                        "Update": {
                            "TableName": self.dynamodb.table_name,
                            "Key": self.key(page),
                            "UpdateExpression": (
                                "SET #s = :working, worker_token = :token, "
                                "lease_expires_at = :expiry, "
                                "attempt_count = :next, children_attempt_count = :children, "
                                "updated_at = :now, dispatch_pending = :no "
                                "REMOVE retry_after"
                            ),
                            "ConditionExpression": (
                                "site_id = :site AND attempt_count = :previous AND "
                                "(#s = :queued OR (#s = :working AND lease_expires_at <= :now)) "
                                "AND (attribute_not_exists(retry_after) OR retry_after <= :now) "
                                f"AND {children_condition} AND {checkpoint_condition}"
                            ),
                            "ExpressionAttributeNames": {"#s": "status"},
                            "ExpressionAttributeValues": {
                                ":site": page["site_id"],
                                ":queued": "QUEUED",
                                ":working": "CRAWLING_AND_PARSING",
                                ":token": token,
                                ":expiry": expires_at,
                                ":next": attempts + (0 if page.get("parsed_content_s3_key") else 1),
                                ":previous": attempts,
                                ":previous_children": children_attempts,
                                ":children": children_attempts + int(parsed),
                                **(
                                    {":parsed_key": page["parsed_content_s3_key"]} if parsed else {}
                                ),
                                ":now": now,
                                ":no": False,
                            },
                        }
                    },
                ]
            )
        except DynamoDBConditionNotMetError:
            return False
        return True

    @staticmethod
    def ownership(token: str, now: str) -> Any:
        """Build the condition requiring a working page and matching valid lease.

        save_result, finish, and retry attach this to their updates so a stale
        worker cannot mutate a page after ownership changes or its lease expires.
        Returns a boto3 condition expression; it does not read or write anything.
        """
        return (
            Attr("status").eq("CRAWLING_AND_PARSING")
            & Attr("worker_token").eq(token)
            & Attr("lease_expires_at").gt(now)
        )

    @staticmethod
    def attempts_exhausted(page: dict, max_attempts: int) -> bool:
        """Select the crawl or children budget for message handling and recovery.

        Legacy pages without children_attempt_count start at zero. A parsed
        checkpoint switches the page to the separate two-attempt children budget.
        """
        if page.get("parsed_content_s3_key"):
            return int(page.get("children_attempt_count", 0)) >= MAX_CHILDREN_ATTEMPTS
        return int(page.get("attempt_count", 0)) >= max_attempts

    def start_children_attempt(self, page: dict, *, token: str, now: str) -> None:
        """Count the first children attempt after parsing in this invocation.

        Resumed checkpoint attempts are counted by claim instead. Requires current
        ownership and remaining budget. Counting before child work lets recovery
        handle crashes during registration or dispatch.
        """
        self.dynamodb.update_item(
            key=self.key(page),
            update_expression=(
                "SET children_attempt_count = if_not_exists(children_attempt_count, :zero) + :one"
            ),
            expression_attribute_values={":zero": 0, ":one": 1},
            condition_expression=(
                self.ownership(token, now)
                & Attr("parsed_content_s3_key").exists()
                & (
                    Attr("children_attempt_count").not_exists()
                    | Attr("children_attempt_count").lt(MAX_CHILDREN_ATTEMPTS)
                )
            ),
        )

    def save_result(self, page: dict, *, token: str, now: str, result: dict) -> None:
        """Save checkpoint attributes only while this worker owns the page.

        The crawler calls this after writing/reusing raw S3 data and again after
        writing/reusing parsed JSON. result is a nonempty mapping of attributes
        to set; page provides the key. Names are aliased to avoid reserved words.
        A stale token or lease raises DynamoDBConditionNotMetError. This method
        does not write S3, finish the page, or mutate the supplied page dictionary.
        """
        names = {f"#f{i}": k for i, k in enumerate(result)}
        values = {f":field{i}": v for i, v in enumerate(result.values())}
        self.dynamodb.update_item(
            key=self.key(page),
            update_expression="SET " + ", ".join(f"#f{i} = :field{i}" for i in range(len(result))),
            expression_attribute_names=names,
            expression_attribute_values=values,
            condition_expression=self.ownership(token, now),
        )

    def finish(self, page: dict, *, token: str, now: str, error: str | None = None) -> None:
        """Finalize an owned page as COMPLETED, or FAILED when error is truthy.

        process_message calls this after parsing and child registration/dispatch,
        or after a nonretryable/exhausted processing failure. Clears lease, retry,
        and dispatch fields and stores at most 1,000 error characters. A lost or
        expired lease raises DynamoDBConditionNotMetError. The caller separately
        checks whether the overall crawl run can complete.
        """
        self.dynamodb.update_item(
            key=self.key(page),
            update_expression=(
                "SET #s = :status, updated_at = :now, last_error = :error "
                "REMOVE worker_token, lease_expires_at, retry_after, "
                "dispatch_pending, dispatch_after"
            ),
            expression_attribute_names={"#s": "status"},
            expression_attribute_values={
                ":status": "FAILED" if error else "COMPLETED",
                ":now": now,
                ":error": (error or "")[:1000],
            },
            condition_expression=self.ownership(token, now),
        )

    def retry(
        self,
        page: dict,
        *,
        token: str,
        now: str,
        retry_after: str,
        error: str,
        refund_children_attempt: bool = False,
    ) -> None:
        """Release page ownership and record a retry deadline after a transient failure.

        process_message calls this before sending a delayed replacement message.
        The page stays CRAWLING_AND_PARSING; its lease expires at now, its worker
        token is removed, and dispatch_pending becomes true. Checkpoints and the
        crawl attempt count are preserved. refund_children_attempt atomically
        refunds a children attempt when discovery-lock contention prevented work.
        retry_after is the earliest allowed reclaim
        time. Requires current ownership and raises on a failed condition; sending
        the retry message is a separate operation recoverable from these fields.
        """
        condition = self.ownership(token, now)
        values = {
            ":now": now,
            ":retry": retry_after,
            ":error": error[:1000],
            ":yes": True,
        }
        refund = ""
        if refund_children_attempt:
            condition &= Attr("children_attempt_count").gt(0)
            values[":one"] = 1
            refund = ", children_attempt_count = children_attempt_count - :one"
        self.dynamodb.update_item(
            key=self.key(page),
            update_expression=(
                "SET lease_expires_at = :now, retry_after = :retry, last_error = :error, "
                f"dispatch_pending = :yes, updated_at = :now{refund} "
                "REMOVE worker_token, dispatch_after"
            ),
            expression_attribute_values=values,
            condition_expression=condition,
        )

    def fail_abandoned(self, page: dict, *, now: str, error: str) -> bool:
        """Fail an unowned page after its crawl or children attempts are exhausted.

        Called by message processing or periodic recovery when the caller has
        already checked the attempt limit. This method compares the supplied
        attempt count rather than checking a configured maximum itself. Requires
        the same site, unchanged checkpoint/counters, a queued/expired working state, and
        no future retry deadline. Clears ownership/retry/dispatch metadata.

        Returns False if a condition fails, protecting a worker that progressed
        since the caller's read. Other database errors propagate.
        """
        checkpoint_condition = Attr("parsed_content_s3_key").not_exists()
        if page.get("parsed_content_s3_key"):
            checkpoint_condition = Attr("parsed_content_s3_key").eq(
                page["parsed_content_s3_key"]
            ) & Attr("children_attempt_count").eq(int(page.get("children_attempt_count", 0)))
        try:
            self.dynamodb.update_item(
                key=self.key(page),
                update_expression=(
                    "SET #s = :failed, last_error = :error, updated_at = :now "
                    "REMOVE worker_token, lease_expires_at, retry_after, "
                    "dispatch_pending, dispatch_after"
                ),
                expression_attribute_names={"#s": "status"},
                expression_attribute_values={
                    ":failed": "FAILED",
                    ":error": error[:1000],
                    ":now": now,
                },
                condition_expression=(
                    Attr("site_id").eq(page["site_id"])
                    & Attr("attempt_count").eq(page["attempt_count"])
                    & checkpoint_condition
                    & (
                        (Attr("status").eq("QUEUED"))
                        | (
                            Attr("status").eq("CRAWLING_AND_PARSING")
                            & Attr("lease_expires_at").lte(now)
                        )
                    )
                    & (Attr("retry_after").not_exists() | Attr("retry_after").lte(now))
                ),
            )
        except DynamoDBConditionNotMetError:
            return False
        return True

    def mark_sent(self, page: dict, *, dispatch_after: str) -> None:
        """Record a successful crawl-queue send and its next recovery-check time.

        dispatch calls this after SQS accepts a message. Sets dispatch_pending
        false and stores dispatch_after, after which recovery may resend stranded
        work. Only queued/working pages with the same attempt count are changed;
        condition failures are ignored. Does not prove receipt or completion,
        and does not make the send and database write atomic.
        """
        with contextlib.suppress(DynamoDBConditionNotMetError):
            self.dynamodb.update_item(
                key=self.key(page),
                update_expression="SET dispatch_pending = :no, dispatch_after = :after",
                expression_attribute_values={":no": False, ":after": dispatch_after},
                condition_expression=(
                    Attr("attempt_count").eq(page["attempt_count"])
                    & Attr("status").is_in(["QUEUED", "CRAWLING_AND_PARSING"])
                ),
            )

    def register_children(
        self,
        parent: dict,
        children: list[dict],
        *,
        runs_table_name: str,
        lock_token: str,
        worker_token: str,
        now: str,
    ) -> None:
        """Insert a child batch while atomically checking both workflow owners.

        _register_children calls this under the run discovery lock, after URL
        deduplication and capacity checks, and before child SQS dispatch. parent
        supplies the run/page keys; children contains prepared QUEUED records;
        lock_token and worker_token identify the run and parent-page owners.

        Requires an active crawling run and both matching unexpired leases at
        now. Every child must be absent at its complete key. All checks and puts
        succeed together or raise, with no partial batch insertion. The caller
        enforces the discovery cap and batches at 50; this method does not count
        pages. Two condition checks leave room for at most 98 child operations.
        """
        # One transaction fences both owners and inserts at most 98 children.
        self.dynamodb.transact_write(
            [
                {
                    "ConditionCheck": {
                        "TableName": runs_table_name,
                        "Key": {
                            "site_id": parent["site_id"],
                            "crawl_run_id": parent["crawl_run_id"],
                        },
                        "ConditionExpression": (
                            "discovery_lock_token = :token AND discovery_lock_expires_at > :now "
                            "AND #s = :working"
                        ),
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {
                            ":token": lock_token,
                            ":now": now,
                            ":working": "CRAWLING_AND_PARSING",
                        },
                    }
                },
                {
                    "ConditionCheck": {
                        "TableName": self.dynamodb.table_name,
                        "Key": self.key(parent),
                        "ConditionExpression": (
                            "#s = :working AND worker_token = :token AND lease_expires_at > :now"
                        ),
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {
                            ":working": "CRAWLING_AND_PARSING",
                            ":token": worker_token,
                            ":now": now,
                        },
                    }
                },
                *[
                    {
                        "Put": {
                            "TableName": self.dynamodb.table_name,
                            "Item": child,
                            "ConditionExpression": "attribute_not_exists(crawl_run_id)",
                        }
                    }
                    for child in children
                ],
            ]
        )

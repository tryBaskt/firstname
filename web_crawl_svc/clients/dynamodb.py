"""Wrap DynamoDB table operations, native-value conversion, and conditional errors."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from botocore.exceptions import ClientError


def to_dynamodb_value(value: Any) -> Any:
    """Recursively convert domain values to boto3 DynamoDB resource-compatible values.

    Convert floats to Decimal, dates to ISO strings, dataclass instances to maps,
    and tuples to lists. String enum values are uppercased; other enum values are
    recursively converted. Stringify map keys and leave other values unchanged.
    This does not produce low-level DynamoDB attribute maps such as {"S": "text"}.
    """
    if isinstance(value, Enum):
        enum_value = value.value
        return enum_value.upper() if isinstance(enum_value, str) else to_dynamodb_value(enum_value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return to_dynamodb_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_dynamodb_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamodb_value(item) for item in value]
    return value


class DynamoDBClientError(Exception):
    """Raised when a DynamoDB operation fails."""

    def __init__(self, message: str, code: str = "DYNAMODB_CLIENT_ERROR") -> None:
        """Store the error message and an application-level operation/error code."""
        super().__init__(message)
        self.code = code


class DynamoDBConditionNotMetError(DynamoDBClientError):
    """Raised when a conditional DynamoDB write is intentionally rejected."""

    def __init__(self, message: str) -> None:
        """Identify a rejected write condition separately from operational failures."""
        super().__init__(message, code="DYNAMODB_CONDITION_NOT_MET")


class DynamoDBClient:
    """Small application wrapper around one boto3 DynamoDB table resource."""

    def __init__(self, table: Any) -> None:
        """Bind an existing boto3 table resource without making an AWS request."""
        self.table = table

    @property
    def table_name(self) -> str:
        """Return the bound table's name for cross-table transaction requests."""
        return self.table.name

    def transact_write(self, items: list[dict[str, Any]]) -> None:
        """Atomically apply transaction operations, potentially across multiple tables.

        items contains boto3 transaction dictionaries such as Put, Update, Delete,
        or ConditionCheck, each with its TableName and operation arguments. Convert
        domain values and use the resource client's native-value serializer.
        Raise DynamoDBConditionNotMetError for cancellations caused only by failed
        conditions; wrap other AWS ClientErrors as DynamoDBClientError. Return None.
        """
        try:
            self.table.meta.client.transact_write_items(TransactItems=to_dynamodb_value(items))
        except ClientError as error:
            reasons = error.response.get("CancellationReasons", [])
            codes = {reason.get("Code", "None") for reason in reasons}
            if (
                error.response.get("Error", {}).get("Code") == "TransactionCanceledException"
                and "ConditionalCheckFailed" in codes
                and codes <= {"None", "ConditionalCheckFailed"}
            ):
                raise DynamoDBConditionNotMetError("Transaction condition was not met.") from error
            raise DynamoDBClientError(
                f"Failed DynamoDB transaction: {error}", code="DYNAMODB_TRANSACTION_FAILED"
            ) from error

    def put_item(
        self,
        item: dict[str, Any],
        condition_expression: Any = None,
    ) -> None:
        """Write a full item, optionally guarded by a DynamoDB condition expression.

        Convert item values before sending. Without a condition, a matching key's
        existing item is replaced. Return None; distinguish rejected conditions
        from other failures with DynamoDBConditionNotMetError.
        """
        kwargs: dict[str, Any] = {"Item": to_dynamodb_value(item)}
        if condition_expression is not None:
            kwargs["ConditionExpression"] = condition_expression
        try:
            self.table.put_item(**kwargs)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == ("ConditionalCheckFailedException"):
                raise DynamoDBConditionNotMetError("DynamoDB put condition was not met.") from error
            raise DynamoDBClientError(
                message=f"Failed to put item into DynamoDB: {error}",
                code="DYNAMODB_PUT_ITEM_FAILED",
            ) from error
        except Exception as error:
            raise DynamoDBClientError(
                message=f"Failed to put item into DynamoDB: {error}",
                code="DYNAMODB_PUT_ITEM_FAILED",
            ) from error

    def get_item(
        self,
        key: dict[str, Any],
        projection_expression: str | None = None,
        consistent_read: bool = False,
    ) -> dict[str, Any] | None:
        """Read one item by its complete primary key, returning None if absent.

        projection_expression optionally selects attributes; consistent_read opts
        into a strongly consistent table read. Return native boto3 values without
        additional conversion, and wrap request failures in DynamoDBClientError.
        """
        kwargs: dict[str, Any] = {
            "Key": key,
            "ConsistentRead": consistent_read,
        }
        if projection_expression:
            kwargs["ProjectionExpression"] = projection_expression

        try:
            response = self.table.get_item(**kwargs)
        except Exception as error:
            raise DynamoDBClientError(
                message=f"Failed to get item from DynamoDB: {error}",
                code="DYNAMODB_GET_ITEM_FAILED",
            ) from error
        return response.get("Item")

    def query(self, key_condition: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Query a table/index by key condition, collecting returned items into a list.

        Forward boto3 options such as IndexName, ScanIndexForward, ConsistentRead,
        FilterExpression, and ExclusiveStartKey. Follow LastEvaluatedKey until
        exhausted unless Limit is supplied, in which case make one request only.
        Limit bounds evaluated items, not guaranteed matches after filtering; no
        continuation key is returned by this wrapper. Wrap request failures.
        """
        query_kwargs = dict(kwargs)
        paginate = "Limit" not in query_kwargs
        items: list[dict[str, Any]] = []
        try:
            while True:
                response = self.table.query(
                    KeyConditionExpression=key_condition,
                    **query_kwargs,
                )
                items.extend(response.get("Items", []))
                last_key = response.get("LastEvaluatedKey")
                if not paginate or not last_key:
                    break
                query_kwargs["ExclusiveStartKey"] = last_key
        except Exception as error:
            raise DynamoDBClientError(
                message=f"Failed to query DynamoDB: {error}",
                code="DYNAMODB_QUERY_FAILED",
            ) from error
        return items

    def scan(self, filter_expression: Any = None, **kwargs: Any) -> list[dict[str, Any]]:
        """Scan all remaining pages and return items matching an optional filter.

        Forward boto3 scan options and advance ExclusiveStartKey using each
        response's LastEvaluatedKey. Unlike query, Limit does not stop pagination:
        it applies per request. Filters do not avoid reading nonmatching items.
        Return native item dictionaries and wrap failures in DynamoDBClientError.
        """
        if filter_expression is not None:
            kwargs["FilterExpression"] = filter_expression

        items: list[dict[str, Any]] = []
        try:
            while True:
                response = self.table.scan(**kwargs)
                items.extend(response.get("Items", []))
                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key
        except Exception as error:
            raise DynamoDBClientError(
                message=f"Failed to scan DynamoDB: {error}",
                code="DYNAMODB_SCAN_FAILED",
            ) from error
        return items

    def update_item(
        self,
        *,
        key: dict[str, Any],
        update_expression: str,
        expression_attribute_names: dict[str, str] | None = None,
        expression_attribute_values: dict[str, Any] | None = None,
        condition_expression: Any = None,
        return_values: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically update an item, optionally requiring a condition to hold.

        key identifies the item; update_expression supplies SET/REMOVE/ADD/DELETE
        operations. Map #name aliases with expression_attribute_names and :value
        placeholders with expression_attribute_values, whose values are converted.
        return_values selects returned Attributes (for example ALL_NEW); otherwise
        normally return None. Raise DynamoDBConditionNotMetError for rejected
        conditions and DynamoDBClientError for other request failures.
        """
        kwargs: dict[str, Any] = {
            "Key": key,
            "UpdateExpression": update_expression,
        }
        if expression_attribute_names:
            kwargs["ExpressionAttributeNames"] = expression_attribute_names
        if expression_attribute_values:
            kwargs["ExpressionAttributeValues"] = to_dynamodb_value(expression_attribute_values)
        if condition_expression is not None:
            kwargs["ConditionExpression"] = condition_expression
        if return_values:
            kwargs["ReturnValues"] = return_values

        try:
            response = self.table.update_item(**kwargs)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == ("ConditionalCheckFailedException"):
                raise DynamoDBConditionNotMetError(
                    "DynamoDB update condition was not met."
                ) from error
            raise DynamoDBClientError(
                message=f"Failed to update item in DynamoDB: {error}",
                code="DYNAMODB_UPDATE_ITEM_FAILED",
            ) from error
        except Exception as error:
            raise DynamoDBClientError(
                message=f"Failed to update item in DynamoDB: {error}",
                code="DYNAMODB_UPDATE_ITEM_FAILED",
            ) from error
        return response.get("Attributes")

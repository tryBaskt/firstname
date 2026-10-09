"""Publish JSON workflow messages to one SQS queue with optional delivery delay."""

import json
from typing import Any


class SQSClientError(Exception):
    """Raised when an SQS operation fails."""

    def __init__(self, message: str, code: str = "SQS_CLIENT_ERROR") -> None:
        """Store a queue failure message and its application-level operation code."""
        super().__init__(message)
        self.code = code


class SQSClient:
    """Application wrapper around one SQS queue."""

    def __init__(self, client: Any, queue_url: str) -> None:
        """Bind an existing boto3 SQS client and queue URL without contacting AWS."""
        self.client = client
        self.queue_url = queue_url

    def send_json(self, message: dict[str, Any], *, delay_seconds: int | None = None) -> str:
        """Serialize a workflow message, send it, and return its nonempty MessageId.

        delay_seconds optionally sets a per-message delay from 0 to 900 seconds;
        None omits the setting so the queue default applies. Invalid ranges raise
        ValueError, and JSON serialization errors propagate before sending.
        Wrap send failures or a missing message ID in SQSClientError. Acceptance
        does not mean processing completed or guarantee single delivery.
        """
        request: dict[str, Any] = {
            "QueueUrl": self.queue_url,
            "MessageBody": json.dumps(message, separators=(",", ":")),
        }
        if delay_seconds is not None:
            if not 0 <= delay_seconds <= 900:
                raise ValueError("delay_seconds must be between 0 and 900")
            request["DelaySeconds"] = delay_seconds

        try:
            response = self.client.send_message(**request)
        except Exception as error:
            raise SQSClientError(
                f"Failed to send SQS message: {error}",
                code="SQS_SEND_MESSAGE_FAILED",
            ) from error

        message_id = response.get("MessageId")
        if not isinstance(message_id, str) or not message_id:
            raise SQSClientError(
                "SQS did not return a message ID.",
                code="SQS_INVALID_RESPONSE",
            )
        return message_id

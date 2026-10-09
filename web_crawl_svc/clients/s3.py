"""Read and write application objects in one S3 bucket with consistent errors."""

from typing import Any

from botocore.exceptions import ClientError


class S3ClientError(Exception):
    """Raised when an S3 operation fails."""

    def __init__(self, message: str, code: str = "S3_CLIENT_ERROR") -> None:
        """Store an S3 failure message and its application-level operation code."""
        super().__init__(message)
        self.code = code


class S3ObjectNotFoundError(S3ClientError):
    """Raised when an S3 object does not exist."""

    def __init__(self, key: str) -> None:
        """Describe the missing object key using the S3_OBJECT_NOT_FOUND code."""
        super().__init__(f"S3 object '{key}' was not found.", code="S3_OBJECT_NOT_FOUND")


class S3Client:
    """Application wrapper around one S3 bucket."""

    def __init__(self, client: Any, bucket_name: str) -> None:
        """Bind an existing boto3 S3 client and bucket name without performing I/O."""
        self.client = client
        self.bucket_name = bucket_name

    def put_bytes(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload bytes at a bucket-relative key and return that key on success.

        Set the supplied MIME content_type and optional string metadata. No
        existence check is performed; callers handle content deduplication.
        Wrap upload failures in S3ClientError with S3_PUT_OBJECT_FAILED.
        """
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=content,
                ContentType=content_type,
                Metadata=metadata or {},
            )
        except Exception as error:
            raise S3ClientError(
                f"Failed to write S3 object '{key}': {error}",
                code="S3_PUT_OBJECT_FAILED",
            ) from error
        return key

    def put_text(
        self,
        *,
        key: str,
        content: str,
        content_type: str = "text/plain; charset=utf-8",
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Encode text as UTF-8 and upload it through put_bytes, returning its key.

        Used for parsed JSON and other crawler artifacts. Forward content_type
        and metadata; upload failures use the same error handling as put_bytes.
        """
        return self.put_bytes(
            key=key,
            content=content.encode("utf-8"),
            content_type=content_type,
            metadata=metadata,
        )

    def get_bytes(self, key: str) -> bytes:
        """Read an object's entire body into memory from its bucket-relative key.

        Raise S3ObjectNotFoundError for recognized missing-object responses and
        S3ClientError for other AWS or body-read failures. No size cap is applied.
        """
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
            return response["Body"].read()
        except ClientError as error:
            self._raise_client_error(error, key, "S3_GET_OBJECT_FAILED")
        except Exception as error:
            raise S3ClientError(
                f"Failed to read S3 object '{key}': {error}",
                code="S3_GET_OBJECT_FAILED",
            ) from error

    def get_text(self, key: str, encoding: str = "utf-8") -> str:
        """Read an object as bytes and decode it with the requested text encoding.

        Preserve get_bytes errors; decoding errors propagate without being wrapped.
        """
        return self.get_bytes(key).decode(encoding)

    def object_exists(self, key: str) -> bool:
        """Check object existence with HEAD without downloading its body.

        Return False only for recognized not-found responses. Permission errors
        and other failures raise S3ClientError rather than implying absence.
        Used by the crawler to reuse content-addressed raw and parsed objects.
        """
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError as error:
            if self._is_not_found(error):
                return False
            raise S3ClientError(
                f"Failed to check S3 object '{key}': {error}",
                code="S3_HEAD_OBJECT_FAILED",
            ) from error
        except Exception as error:
            raise S3ClientError(
                f"Failed to check S3 object '{key}': {error}",
                code="S3_HEAD_OBJECT_FAILED",
            ) from error

    @staticmethod
    def _is_not_found(error: ClientError) -> bool:
        """Recognize 404, NoSuchKey, and NotFound AWS error codes as missing objects."""
        code = str(error.response.get("Error", {}).get("Code", ""))
        return code in {"404", "NoSuchKey", "NotFound"}

    @classmethod
    def _raise_client_error(cls, error: ClientError, key: str, code: str) -> None:
        """Raise a missing-object or operation-specific error, preserving its cause."""
        if cls._is_not_found(error):
            raise S3ObjectNotFoundError(key) from error
        raise S3ClientError(f"S3 operation failed for '{key}': {error}", code=code) from error

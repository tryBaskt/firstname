from io import BytesIO
from typing import Any

import pytest
from botocore.exceptions import ClientError

from web_crawl_svc.clients.s3 import S3Client, S3ClientError, S3ObjectNotFoundError


class FakeS3BotoClient:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_: Any) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, BytesIO]:
        try:
            content = self.objects[(Bucket, Key)]
        except KeyError as error:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                "GetObject",
            ) from error
        return {"Body": BytesIO(content)}

    def head_object(self, *, Bucket: str, Key: str) -> None:
        if (Bucket, Key) not in self.objects:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "missing"}},
                "HeadObject",
            )

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, *_: Any, **__: Any) -> str:
        return "https://example.com/download"


def test_put_and_get_text() -> None:
    boto_client = FakeS3BotoClient()
    client = S3Client(boto_client, "test-bucket")

    key = client.put_text(key="raw/site/page.html", content="<html></html>")

    assert key == "raw/site/page.html"
    assert client.get_text(key) == "<html></html>"
    assert client.object_exists(key)


def test_missing_object_returns_false_or_raises_not_found() -> None:
    client = S3Client(FakeS3BotoClient(), "test-bucket")

    assert not client.object_exists("missing.txt")
    with pytest.raises(S3ObjectNotFoundError):
        client.get_bytes("missing.txt")


def test_unknown_s3_errors_are_wrapped() -> None:
    class FailingClient:
        def put_object(self, **_: Any) -> None:
            raise RuntimeError("unavailable")

    with pytest.raises(S3ClientError) as error:
        S3Client(FailingClient(), "test-bucket").put_bytes(
            key="raw/page.html",
            content=b"content",
            content_type="text/html",
        )

    assert error.value.code == "S3_PUT_OBJECT_FAILED"

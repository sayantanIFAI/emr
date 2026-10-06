"""The S3 layer (``cdi_adapter.storage``) against botocore's Stubber: no object store needed.

Only the S3 calls the product makes are covered, which is also the list a replacement object store
must support: head/create bucket, put/get object, presigned GET. Whether a particular server
(SeaweedFS, RustFS, ...) honours them is proved by running the integration tests against it, not
here.
"""
from __future__ import annotations

import io
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore.client import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber

from cdi_adapter import storage
from cdi_adapter.config import settings


@pytest.fixture
def s3(monkeypatch):
    client = boto3.client(
        "s3", endpoint_url="http://store.test:9000", aws_access_key_id="k", aws_secret_access_key="s",
        region_name="us-east-1", config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    monkeypatch.setattr(storage, "_client", client)
    with Stubber(client) as stub:
        yield client, stub


def _err(code: str, status: int = 400) -> dict:
    return {"service_error_code": code, "http_status_code": status}


def test_ping_is_true_when_the_bucket_answers(s3):
    _, stub = s3
    stub.add_response("head_bucket", {}, {"Bucket": settings.s3_bucket})
    assert storage.ping() is True
    stub.assert_no_pending_responses()


def test_ping_never_needs_the_account_wide_list_buckets_permission(s3):
    """A key scoped to one bucket cannot ListBuckets: the old check reported it as down."""
    _, stub = s3
    stub.add_response("head_bucket", {}, {"Bucket": settings.s3_bucket})
    storage.ping()          # a ListBuckets call would raise StubResponseError (not stubbed)


def test_ping_counts_a_missing_bucket_as_up_but_a_refused_key_as_down(s3):
    _, stub = s3
    stub.add_client_error("head_bucket", "404", http_status_code=404)
    assert storage.ping() is True
    stub.add_client_error("head_bucket", "403", http_status_code=403)
    assert storage.ping() is False


def test_ping_is_false_when_the_store_is_unreachable(s3, monkeypatch):
    client, _ = s3

    def refuse(*_a, **_k):
        raise EndpointConnectionError(endpoint_url="http://store.test:9000")

    monkeypatch.setattr(client, "head_bucket", refuse)
    assert storage.ping() is False


def test_ensure_bucket_creates_only_when_missing(s3):
    _, stub = s3
    stub.add_response("head_bucket", {}, {"Bucket": settings.s3_bucket})
    storage.ensure_bucket()
    stub.add_client_error("head_bucket", "404", http_status_code=404)
    stub.add_response("create_bucket", {}, {"Bucket": settings.s3_bucket})
    storage.ensure_bucket()
    stub.assert_no_pending_responses()


def test_ensure_bucket_tolerates_losing_the_creation_race(s3):
    _, stub = s3
    stub.add_client_error("head_bucket", "404", http_status_code=404)
    stub.add_client_error("create_bucket", "BucketAlreadyOwnedByYou", http_status_code=409)
    storage.ensure_bucket()


def test_ensure_bucket_still_raises_on_a_real_failure(s3):
    _, stub = s3
    stub.add_client_error("head_bucket", "404", http_status_code=404)
    stub.add_client_error("create_bucket", "AccessDenied", http_status_code=403)
    with pytest.raises(ClientError):
        storage.ensure_bucket()


def test_put_and_get_round_trip_the_exact_bytes(s3):
    _, stub = s3
    data = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    stub.add_response("put_object", {}, {
        "Bucket": settings.s3_bucket, "Key": "documents/ab/abc/original.png", "Body": data,
        "ContentType": "image/png"})
    uri = storage.put_bytes("documents/ab/abc/original.png", data, "image/png")
    assert uri == f"s3://{settings.s3_bucket}/documents/ab/abc/original.png"
    stub.add_response("get_object", {"Body": _Body(data)}, {
        "Bucket": settings.s3_bucket, "Key": "documents/ab/abc/original.png"})
    assert storage.get_bytes(storage.key_from_uri(uri)) == data


class _Body(io.BytesIO):
    """botocore's StreamingBody stand-in: the Stubber validates the shape, not the type."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)

    def read(self, *a, **k):
        return super().read(*a, **k)


def test_key_from_uri_only_strips_our_bucket_prefix():
    assert storage.key_from_uri(f"s3://{settings.s3_bucket}/a/b.png") == "a/b.png"
    assert storage.key_from_uri("a/b.png") == "a/b.png"
    assert storage.key_from_uri("s3://other-bucket/a/b.png") == "s3://other-bucket/a/b.png"


def test_presigned_get_is_a_path_style_sigv4_url_with_the_requested_expiry(s3):
    url = storage.presign_get("documents/ab/abc/pages/0001.png", expires=900)
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.scheme == "http" and parsed.netloc == "store.test:9000"
    assert parsed.path == f"/{settings.s3_bucket}/documents/ab/abc/pages/0001.png"
    assert q["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"] and q["X-Amz-Expires"] == ["900"]
    assert "X-Amz-Signature" in q

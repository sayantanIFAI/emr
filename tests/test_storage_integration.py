"""The S3 layer against a REAL object store (SeaweedFS in the stack; any S3 server works).

Needs only the store, not the database. Auto-skips unless ``CDI_S3_ENDPOINT_URL`` is set and the
store answers. The bucket must already exist (compose ``createbuckets`` / ``start_objectstore.sh``
create it). The least-privilege checks also need the admin identity:
``CDI_S3_ADMIN_ACCESS_KEY`` / ``CDI_S3_ADMIN_SECRET_KEY``.

Run:  CDI_S3_ENDPOINT_URL=http://127.0.0.1:9000 pytest tests/test_storage_integration.py
"""
from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request

import pytest
from botocore.exceptions import ClientError

# not `integration`: that marker means "needs the database too" and is skipped without it
pytestmark = pytest.mark.objectstore


@pytest.fixture(scope="module")
def store():
    if not os.environ.get("CDI_S3_ENDPOINT_URL"):
        pytest.skip("CDI_S3_ENDPOINT_URL not set")
    from cdi_adapter import storage

    if not storage.ping():
        pytest.skip("object store not reachable with the configured key")
    return storage


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + os.urandom(250_000)


def test_ping_and_ensure_bucket_with_the_application_key(store):
    assert store.ping() is True
    store.ensure_bucket()        # the bucket exists: head_bucket succeeds, nothing is created


def test_put_get_round_trip_is_byte_exact_and_keeps_the_content_type(store):
    data = _png()
    key = f"itest/{hashlib.sha256(data).hexdigest()}/page.png"
    uri = store.put_bytes(key, data, "image/png")
    assert uri == store.object_uri(key) and store.key_from_uri(uri) == key
    assert store.get_bytes(key) == data
    head = store.get_s3().head_object(Bucket=store.settings.s3_bucket, Key=key)
    assert head["ContentType"] == "image/png" and head["ContentLength"] == len(data)


def test_overwrite_replaces_and_a_missing_key_raises_nosuchkey(store):
    key = "itest/overwrite.bin"
    store.put_bytes(key, b"first")
    store.put_bytes(key, b"second")
    assert store.get_bytes(key) == b"second"
    with pytest.raises(ClientError) as exc:
        store.get_bytes("itest/does-not-exist.bin")
    assert exc.value.response["Error"]["Code"] == "NoSuchKey"


def test_presigned_get_works_without_credentials_and_cannot_be_tampered_with(store):
    data = _png()
    key = f"itest/{hashlib.sha256(data).hexdigest()}/presigned.png"
    store.put_bytes(key, data, "image/png")
    url = store.presign_get(key, expires=300)
    with urllib.request.urlopen(url, timeout=30) as resp:
        assert resp.status == 200 and resp.read() == data
    with pytest.raises(urllib.error.HTTPError) as bad:
        urllib.request.urlopen(url.replace("X-Amz-Signature=", "X-Amz-Signature=00"), timeout=30)
    assert bad.value.code in (400, 403)


def test_an_unsigned_request_is_refused(store):
    key = "itest/unsigned.bin"
    store.put_bytes(key, b"secret")
    base = store.settings.s3_endpoint_url.rstrip("/")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/{store.settings.s3_bucket}/{key}", timeout=30)
    assert exc.value.code in (401, 403)


def test_the_application_key_is_scoped_to_its_own_bucket(store):
    """Least privilege (docs/object-store.md): the application identity can use the one bucket and
    nothing else. Needs the admin key only to create a second bucket to be refused access to."""
    admin_key = os.environ.get("CDI_S3_ADMIN_ACCESS_KEY")
    admin_secret = os.environ.get("CDI_S3_ADMIN_SECRET_KEY")
    if not (admin_key and admin_secret):
        pytest.skip("admin identity not provided")
    import boto3
    from botocore.client import Config

    s = store.settings
    admin = boto3.client("s3", endpoint_url=s.s3_endpoint_url, aws_access_key_id=admin_key,
                         aws_secret_access_key=admin_secret, region_name=s.s3_region,
                         config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
    other = "itest-other-bucket"
    try:
        admin.create_bucket(Bucket=other)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
    try:
        app = store.get_s3()
        assert [b["Name"] for b in app.list_buckets()["Buckets"]] == [s.s3_bucket]  # others not shown
        with pytest.raises(ClientError) as denied:
            app.put_object(Bucket=other, Key="x", Body=b"1")
        assert denied.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
        with pytest.raises(ClientError) as cannot_create:
            app.create_bucket(Bucket="itest-created-by-app")
        assert cannot_create.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    finally:
        admin.delete_bucket(Bucket=other)

from __future__ import annotations

import io

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import settings
from .logging import get_logger

log = get_logger(__name__)

_client = None


def _fs():
    """The folder store when ``CDI_OBJECT_STORE=filesystem`` (the second ObjectStore adapter), else None: the
    S3 code below is used. Every caller keeps using this module's functions: nothing in ingest knows which."""
    if (settings.object_store or "s3").strip().lower() == "filesystem":
        from .storage_fs import FilesystemStore

        return FilesystemStore()
    return None


def get_s3():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.s3_use_path_style else "auto"},
            ),
        )
    return _client


_MISSING = {"404", "NoSuchBucket", "NotFound"}
_RACE = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def ensure_bucket() -> None:
    if (fs := _fs()) is not None:
        return fs.ensure()
    s3 = get_s3()
    try:
        s3.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        log.info("creating_bucket", bucket=settings.s3_bucket)
        try:
            s3.create_bucket(Bucket=settings.s3_bucket)
        except ClientError as exc:
            if _code(exc) not in _RACE:      # another process created it first: fine
                raise


def ensure_bucket_when_ready(timeout_s: float = 60.0, every_s: float = 1.0) -> None:
    """``ensure_bucket`` for a store that is still starting: retry connection failures until
    ``timeout_s``. A refusal (bad key, no permission to create) is not retried: it raises."""
    import time

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            ensure_bucket()
            return
        except ClientError:
            raise
        except Exception as exc:  # connection refused / DNS / reset while the store starts
            if time.monotonic() >= deadline:
                raise
            log.info("object_store_not_ready", error=type(exc).__name__)
            time.sleep(every_s)


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    if (fs := _fs()) is not None:
        return fs.put(key, data, content_type)
    get_s3().put_object(
        Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type
    )
    return object_uri(key)


def get_bytes(key: str) -> bytes:
    if (fs := _fs()) is not None:
        return fs.get(key)
    obj = get_s3().get_object(Bucket=settings.s3_bucket, Key=key)
    return obj["Body"].read()


def object_uri(key: str) -> str:
    if (fs := _fs()) is not None:
        return fs.uri(key)
    return f"s3://{settings.s3_bucket}/{key}"


def key_from_uri(uri: str) -> str:
    if uri.startswith("file://"):                 # a folder-store URI (kept readable after a switch back)
        from .storage_fs import FilesystemStore

        return FilesystemStore.key_from_uri(uri)
    prefix = f"s3://{settings.s3_bucket}/"
    return uri[len(prefix):] if uri.startswith(prefix) else uri


def presign_get(key: str, expires: int = 3600) -> str:
    if _fs() is not None:
        raise NotImplementedError("the folder store has no pre-signed links: read the bytes with get_bytes()")
    return get_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires,
    )


def ping() -> bool:
    """Is the object store reachable with these credentials? Asks about OUR bucket only
    (``head_bucket``), so a least-privilege key scoped to that bucket passes: ``list_buckets``
    needs an account-wide permission. A missing bucket still counts as up (``ensure_bucket``
    creates it); a 403 (wrong or revoked key) or a connection failure does not."""
    if (fs := _fs()) is not None:
        return fs.ping()
    try:
        get_s3().head_bucket(Bucket=settings.s3_bucket)
        return True
    except ClientError as exc:
        return _code(exc) in _MISSING
    except Exception:  # noqa: BLE001 - connection refused, DNS, timeout
        return False


def stream(key: str) -> io.BytesIO:
    return io.BytesIO(get_bytes(key))


class S3Store:
    """The S3-API adapter behind the ``ObjectStore`` interface (swap/points.py). It calls this module's S3
    code directly, whatever ``CDI_OBJECT_STORE`` says, so the contract test can run it next to the folder store."""

    name = "s3"

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        get_s3().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
        return f"s3://{settings.s3_bucket}/{key}"

    def get(self, key: str) -> bytes:
        try:
            return get_s3().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if _code(exc) in ("NoSuchKey", "404", "NotFound"):
                raise KeyError(key) from exc
            raise

    def uri(self, key: str) -> str:
        return f"s3://{settings.s3_bucket}/{key}"

    def key_from_uri(self, uri: str) -> str:
        prefix = f"s3://{settings.s3_bucket}/"
        return uri[len(prefix):] if uri.startswith(prefix) else uri

    def ping(self) -> bool:
        try:
            get_s3().head_bucket(Bucket=settings.s3_bucket)
            return True
        except ClientError as exc:
            return _code(exc) in _MISSING
        except Exception:  # noqa: BLE001
            return False

    def ensure(self) -> None:
        s3 = get_s3()
        try:
            s3.head_bucket(Bucket=settings.s3_bucket)
        except ClientError:
            try:
                s3.create_bucket(Bucket=settings.s3_bucket)
            except ClientError as exc:
                if _code(exc) not in _RACE:
                    raise


if __name__ == "__main__":  # `python -m cdi_adapter.storage`: create the bucket (compose init job)
    ensure_bucket_when_ready()
    print(f"bucket ready: {settings.s3_bucket}")

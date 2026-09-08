from __future__ import annotations

import io

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import settings
from .logging import get_logger

log = get_logger(__name__)

_client = None


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


def ensure_bucket() -> None:
    s3 = get_s3()
    try:
        s3.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        log.info("creating_bucket", bucket=settings.s3_bucket)
        s3.create_bucket(Bucket=settings.s3_bucket)


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    get_s3().put_object(
        Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type
    )
    return object_uri(key)


def get_bytes(key: str) -> bytes:
    obj = get_s3().get_object(Bucket=settings.s3_bucket, Key=key)
    return obj["Body"].read()


def object_uri(key: str) -> str:
    return f"s3://{settings.s3_bucket}/{key}"


def key_from_uri(uri: str) -> str:
    prefix = f"s3://{settings.s3_bucket}/"
    return uri[len(prefix):] if uri.startswith(prefix) else uri


def presign_get(key: str, expires: int = 3600) -> str:
    return get_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires,
    )


def ping() -> bool:
    try:
        get_s3().list_buckets()
        return True
    except Exception:
        return False


def stream(key: str) -> io.BytesIO:
    return io.BytesIO(get_bytes(key))

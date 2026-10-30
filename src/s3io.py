"""S3-compatible helpers for local RustFS."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

def s3_client():
    endpoint = os.getenv("S3_ENDPOINT", "http://localhost:9000").rstrip("/")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("S3_ACCESS_KEY", ""),
        aws_secret_access_key=os.getenv("S3_SECRET_KEY", ""),
        region_name=os.getenv("S3_REGION", "us-east-1"),
    )

def ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if status not in (404, 400) and code not in ("404", "NoSuchBucket", "NotFound"):
            raise
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise

def upload_file(local_path: str | Path, bucket: str, key: str, content_type: str | None = None) -> None:
    client = s3_client()
    extra = {"ContentType": content_type} if content_type else None
    if extra:
        client.upload_file(str(local_path), bucket, key, ExtraArgs=extra)
    else:
        client.upload_file(str(local_path), bucket, key)

def upload_bytes(data: bytes, bucket: str, key: str, content_type: str = "application/octet-stream") -> None:
    s3_client().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


def upload_json(value: Any, bucket: str, key: str) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    upload_bytes(payload, bucket, key, "application/json; charset=utf-8")
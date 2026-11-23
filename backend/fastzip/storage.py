from functools import lru_cache
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from fastzip.config import settings


class Storage:
    def __init__(self, backup=False):
        s = settings()
        self.bucket = s.backup_bucket if backup else s.s3_bucket
        access = (s.backup_access_key or s.s3_access_key) if backup else s.s3_access_key
        secret = (s.backup_secret_key or s.s3_secret_key) if backup else s.s3_secret_key
        arguments = dict(
            region_name=s.s3_region,
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2},
                s3={"addressing_style": "path"},
            ),
        )
        self.client = boto3.client("s3", endpoint_url=s.s3_endpoint or None, **arguments)
        self.signer = boto3.client(
            "s3", endpoint_url=s.s3_public_endpoint or s.s3_endpoint or None, **arguments
        )

    def put(self, path, key: str, content_type: str):
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    def get(self, key: str, path):
        self.client.download_file(self.bucket, key, str(path))

    def delete(self, key: str):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def size_bytes(self, key: str) -> int | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError as exc:
            if str(exc.response["Error"]["Code"]) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

    def exists(self, key: str) -> bool:
        return self.size_bytes(key) is not None

    def objects(self, prefix=""):
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            yield from page.get("Contents", [])

    def signed_url(self, key: str, filename: str, expires_in: int = 300) -> str:
        return self.signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": "attachment; filename*=UTF-8''"
                + quote(filename, safe=""),
            },
            ExpiresIn=expires_in,
        )


@lru_cache
def storage() -> Storage:
    return Storage()

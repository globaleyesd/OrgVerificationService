"""Where the original uploaded files are kept: a local folder, or the private documents bucket on AWS."""
from __future__ import annotations

import re
from pathlib import Path

from .config import Config

_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_name(name: str) -> str:
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    return (_UNSAFE.sub("_", base).strip(" .") or "file")[:120]


class LocalFileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def save(self, key: str, data: bytes) -> str:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid file key")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"local:{key}"

    def read(self, stored: str) -> bytes:
        path = (self.root / stored.removeprefix("local:")).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid file key")
        return path.read_bytes()


class S3FileStore:
    def __init__(self, bucket: str, client=None):
        self.bucket = bucket
        if client is None:
            import boto3
            client = boto3.client("s3")
        self.client = client

    def save(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ServerSideEncryption="AES256")
        return f"s3://{self.bucket}/{key}"

    def read(self, stored: str) -> bytes:
        prefix = f"s3://{self.bucket}/"
        if not stored.startswith(prefix):
            raise ValueError("File is not in this bucket")
        return self.client.get_object(Bucket=self.bucket, Key=stored[len(prefix):])["Body"].read()


def make_file_store(cfg: Config):
    s = cfg.storage
    return S3FileStore(s.s3_bucket) if s.backend == "s3" else LocalFileStore(s.local_path)

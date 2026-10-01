"""Credential storage behind one small interface.

Backends (credentials.backend):
  file  a local folder (default `creds/`, git-ignored and docker-ignored): local runs
  s3    a PRIVATE, server-only S3 bucket: the AWS setup (see deploy/README.md)
  (secrets_manager is planned, not built)

What is stored: salted password hashes (service switch, user accounts) and the key that signs
sign-in cookies. Records are created ON the server (or on your machine for local runs): the
deploy script can never upload them.

Missing record  -> read() returns None.
Unreadable / corrupt / service error -> CredentialStoreError (callers must fail safe and
never treat a failure as "nothing stored", or a glitch could reset a password).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from .config import Config, ConfigError

_NAME = re.compile(r"[a-z0-9_-]+(/[a-z0-9_-]+)*")


class CredentialStoreError(Exception):
    pass


class CredentialStore(Protocol):
    def read(self, name: str) -> dict | None: ...
    def write(self, name: str, data: dict) -> None: ...
    def list(self, prefix: str = "") -> list[str]: ...


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("Invalid credential name")
    return name


class FileCredentialStore:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def _path(self, name: str) -> Path:
        return self.dir / f"{_check_name(name)}.json"

    def read(self, name: str) -> dict | None:
        p = self._path(name)
        try:
            data = json.loads(p.read_text())
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as e:
            raise CredentialStoreError(f"Credential file {p.name} is unreadable") from e
        if not isinstance(data, dict):
            raise CredentialStoreError(f"Credential file {p.name} is malformed")
        return data

    def write(self, name: str, data: dict) -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        for d in {self.dir, path.parent}:
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass   # e.g. some mounted Windows folders
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def list(self, prefix: str = "") -> list[str]:
        if not self.dir.exists():
            return []
        names = sorted(p.relative_to(self.dir).with_suffix("").as_posix() for p in self.dir.rglob("*.json"))
        return [n for n in names if n.startswith(prefix)]


class S3CredentialStore:
    """One small JSON object per record in a private bucket. The server's IAM role is the only
    identity allowed to touch it (bucket policy in deploy/cloudformation/stack.yaml)."""

    def __init__(self, bucket: str, prefix: str = "", client=None, region: str | None = None):
        if not bucket:
            raise ConfigError("credentials.s3_bucket is empty")
        self.bucket = bucket
        self.prefix = prefix
        if client is None:
            import boto3   # imported here so local runs and tests don't need it
            client = boto3.client("s3", region_name=region) if region else boto3.client("s3")
        self.client = client

    def _key(self, name: str) -> str:
        return f"{self.prefix}{_check_name(name)}.json"

    def read(self, name: str) -> dict | None:
        key = self._key(name)   # a bad name is a programming error, not a store failure
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
            data = json.loads(obj["Body"].read())
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
            if code in ("NoSuchKey", "404"):
                return None
            raise CredentialStoreError(f"Could not read credential record '{name}' ({type(e).__name__})") from e
        if not isinstance(data, dict):
            raise CredentialStoreError(f"Credential record '{name}' is malformed")
        return data

    def write(self, name: str, data: dict) -> None:
        key = self._key(name)
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(data).encode(),
                                   ContentType="application/json", ServerSideEncryption="AES256")
        except Exception as e:
            raise CredentialStoreError(f"Could not write credential record '{name}' ({type(e).__name__})") from e

    def list(self, prefix: str = "") -> list[str]:
        names, token = [], None
        try:
            while True:
                kw = {"Bucket": self.bucket, "Prefix": self.prefix + prefix}
                if token:
                    kw["ContinuationToken"] = token
                resp = self.client.list_objects_v2(**kw)
                for item in resp.get("Contents", []):
                    k = item["Key"][len(self.prefix):]
                    if k.endswith(".json"):
                        names.append(k[:-5])
                if not resp.get("IsTruncated"):
                    return sorted(names)
                token = resp.get("NextContinuationToken")
        except Exception as e:
            raise CredentialStoreError(f"Could not list credential records ({type(e).__name__})") from e


def make_store(cfg: Config) -> CredentialStore:
    c = cfg.credentials
    if c.backend == "file":
        return FileCredentialStore(c.path)
    if c.backend == "s3":
        return S3CredentialStore(c.s3_bucket, c.s3_prefix)
    raise ConfigError(
        "credentials.backend 'secrets_manager' is planned but not built yet. Use 'file' (local) or 's3' (AWS)."
    )

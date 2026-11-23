"""Media storage abstraction: local filesystem (development) or S3-compatible (S3, R2, MinIO).

Object keys are opaque, server-generated strings (never user input), laid out as::

    users/{user_id}/sources/{source_id}/original{ext}
    users/{user_id}/sources/{source_id}/thumb.jpg
    users/{user_id}/clips/{clip_id}/proxy.mp4        # landscape preview for the editors
    users/{user_id}/clips/{clip_id}/render-{hash}.mp4 # final 9:16 export
    users/{user_id}/clips/{clip_id}/thumb.jpg
    users/{user_id}/fonts/{font_id}{ext}
    users/{user_id}/assets/{asset_id}{ext}

Browsers never get raw keys: :meth:`Storage.url` returns a short-lived signed URL (an
HMAC token served by ``GET /api/v1/media/{token}`` for local storage, or an S3
presigned URL).
"""

from __future__ import annotations

import logging
import mimetypes
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.core.errors import NotFoundError

log = logging.getLogger(__name__)
MEDIA_TOKEN_PURPOSE = "media"


class Storage(Protocol):
    backend: str

    def put_file(self, local_path: Path, key: str, content_type: str | None = None) -> None: ...
    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> None: ...
    def download(self, key: str, dest: Path) -> Path: ...
    def local_path(self, key: str) -> Path | None: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...
    def url(self, key: str, ttl_seconds: int | None = None, download_name: str | None = None) -> str: ...


def _guess_type(key: str) -> str:
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


class LocalStorage:
    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError("Storage key escapes storage root")
        return p

    def put_file(self, local_path: Path, key: str, content_type: str | None = None) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if Path(local_path).resolve() != dest:
            tmp = dest.with_suffix(dest.suffix + f".{uuid.uuid4().hex}.part")
            shutil.copyfile(local_path, tmp)
            tmp.replace(dest)

    def move_file(self, local_path: Path, key: str) -> None:
        """Move (instead of copy) a local file into storage — used for large uploads."""
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(local_path), str(dest))

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + f".{uuid.uuid4().hex}.part")
        tmp.write_bytes(data)
        tmp.replace(dest)

    def download(self, key: str, dest: Path) -> Path:
        src = self._path(key)
        if not src.exists():
            log.warning("Stored object not found: %s", key)
            raise NotFoundError("The stored media file is missing")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

    def local_path(self, key: str) -> Path | None:
        p = self._path(key)
        return p if p.exists() else None

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def size(self, key: str) -> int:
        p = self._path(key)
        if not p.exists():
            log.warning("Stored object not found: %s", key)
            raise NotFoundError("The stored media file is missing")
        return p.stat().st_size

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def delete_prefix(self, prefix: str) -> None:
        p = self._path(prefix)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()

    def url(self, key: str, ttl_seconds: int | None = None, download_name: str | None = None) -> str:
        from app.core.security import sign_payload

        settings = get_settings()
        ttl = ttl_seconds or settings.media_url_ttl_seconds
        payload: dict = {"k": key}
        if download_name:
            payload["dn"] = download_name
        token = sign_payload(payload, ttl, MEDIA_TOKEN_PURPOSE)
        return f"{settings.backend_public_url.rstrip('/')}{settings.api_prefix}/media/{token}"


class S3Storage:
    backend = "s3"

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = get_settings()
        if not s.s3_bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 requires S3_BUCKET")
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url or None,
            region_name=s.s3_region or None,
            aws_access_key_id=s.s3_access_key_id or None,
            aws_secret_access_key=s.s3_secret_access_key or None,
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "adaptive"}),
        )

    def put_file(self, local_path: Path, key: str, content_type: str | None = None) -> None:
        self.client.upload_file(str(local_path), self.bucket, key,
                                ExtraArgs={"ContentType": content_type or _guess_type(key)})

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type or _guess_type(key))

    def download(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(dest))
        return dest

    def local_path(self, key: str) -> Path | None:
        return None

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def size(self, key: str) -> int:
        return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})

    def url(self, key: str, ttl_seconds: int | None = None, download_name: str | None = None) -> str:
        params: dict = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        return self.client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=ttl_seconds or get_settings().s3_presign_ttl_seconds
        )


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(settings.local_storage_dir)


def reset_storage_cache() -> None:
    get_storage.cache_clear()


@contextmanager
def materialize(key: str, suffix: str | None = None) -> Iterator[Path]:
    """Yield a local filesystem path for ``key``. Local storage returns the file in place;
    remote storage downloads to a temp file that is removed afterwards."""
    storage = get_storage()
    local = storage.local_path(key)
    if local is not None:
        yield local
        return
    settings = get_settings()
    tmpdir = Path(tempfile.mkdtemp(prefix="cf-mat-", dir=settings.temp_dir))
    try:
        dest = tmpdir / ("object" + (suffix or Path(key).suffix))
        storage.download(key, dest)
        yield dest
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@contextmanager
def temp_workdir(prefix: str = "cf-job-") -> Iterator[Path]:
    """Per-job scratch directory under TEMP_DIR, always cleaned up."""
    settings = get_settings()
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix=prefix, dir=settings.temp_dir))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def source_key(user_id: uuid.UUID, source_id: uuid.UUID, ext: str) -> str:
    return f"users/{user_id}/sources/{source_id}/original{ext.lower()}"


def source_thumb_key(user_id: uuid.UUID, source_id: uuid.UUID) -> str:
    return f"users/{user_id}/sources/{source_id}/thumb.jpg"


def clip_key(user_id: uuid.UUID, clip_id: uuid.UUID, name: str) -> str:
    return f"users/{user_id}/clips/{clip_id}/{name}"


def user_asset_key(user_id: uuid.UUID, kind: str, asset_id: uuid.UUID, ext: str) -> str:
    return f"users/{user_id}/{kind}/{asset_id}{ext.lower()}"

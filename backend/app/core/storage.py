"""Storage abstraction: local filesystem (default) or Supabase Storage.

Artifacts are content-addressed by SHA-256 so evidence snapshots can pin the
exact bytes.  ``STORAGE_MODE=supabase`` uploads to the ``spectra-evidence``
bucket using the service-role key (server side only) and returns a signed URL;
the local demo keeps everything under ``data/uploads``.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import settings


@dataclass
class StoredObject:
    key: str
    local_path: Path
    public_url: str | None
    sha256: str
    size: int


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Storage:
    def __init__(self) -> None:
        self.root = settings.UPLOAD_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.bucket = "spectra-evidence"

    # ------------------------------------------------------------------ local
    def local_path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("path traversal blocked")
        return p

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> StoredObject:
        path = self.local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        digest = sha256_bytes(data)
        url = None
        if settings.use_supabase_storage:
            url = self._supabase_upload(key, data, content_type)
        return StoredObject(key=key, local_path=path, public_url=url, sha256=digest, size=len(data))

    def put_file(self, key: str, src: Path, *, content_type: str = "application/octet-stream") -> StoredObject:
        return self.put_bytes(key, src.read_bytes(), content_type=content_type)

    def copy_into(self, src: Path, key: str) -> StoredObject:
        path = self.local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != path.resolve():
            shutil.copyfile(src, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        url = None
        if settings.use_supabase_storage:
            url = self._supabase_upload(key, path.read_bytes(), "application/octet-stream")
        return StoredObject(key=key, local_path=path, public_url=url, sha256=digest, size=path.stat().st_size)

    # --------------------------------------------------------------- supabase
    def _supabase_upload(self, key: str, data: bytes, content_type: str) -> str | None:
        try:
            url = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/{self.bucket}/{key}"
            headers = {
                "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
                "apikey": settings.SUPABASE_SERVICE_KEY,
                "Content-Type": content_type,
                "x-upsert": "true",
            }
            with httpx.Client(timeout=30) as client:
                r = client.post(url, content=data, headers=headers)
            if r.status_code in (200, 201):
                return f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{self.bucket}/{key}"
        except Exception:
            return None
        return None

    def signed_url(self, key: str) -> str | None:
        if not settings.use_supabase_storage:
            return None
        try:
            url = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/sign/{self.bucket}/{key}"
            headers = {"Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
                       "apikey": settings.SUPABASE_SERVICE_KEY}
            with httpx.Client(timeout=20) as client:
                r = client.post(url, json={"expiresIn": 3600}, headers=headers)
                if r.status_code == 200:
                    return settings.SUPABASE_URL.rstrip("/") + "/storage/v1" + r.json()["signedURL"]
        except Exception:
            return None
        return None


storage = Storage()

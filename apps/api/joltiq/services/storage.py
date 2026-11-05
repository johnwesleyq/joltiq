"""Object storage: S3-compatible (MinIO/AWS/R2) when configured, local filesystem otherwise.
Keys are always prefixed by org_id so storage is tenant-scoped."""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from ..config import get_settings

ALLOWED_TYPES = {
    "application/pdf": b"%PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": b"PK\x03\x04",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": b"PK\x03\x04",
    "text/plain": None, "text/markdown": None, "text/csv": None, "text/html": None,
    "image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff", "application/json": None,
}
EXT_TYPES = {
    ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".txt": "text/plain",
    ".md": "text/markdown", ".markdown": "text/markdown", ".csv": "text/csv", ".html": "text/html", ".htm": "text/html",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".log": "text/plain", ".json": "application/json",
    ".vtt": "text/plain", ".srt": "text/plain",
}
BLOCKED_MAGIC = [b"MZ", b"\x7fELF", b"#!", b"\xca\xfe\xba\xbe"]  # executables/scripts


class UploadRejected(ValueError):
    pass


def sniff(filename: str, data: bytes, max_mb: int | None = None) -> str:
    """Validate an upload by extension AND magic bytes; reject executables, macro-enabled and oversized files."""
    max_mb = max_mb or get_settings().max_upload_mb
    if len(data) > max_mb * 1024 * 1024:
        raise UploadRejected(f"file exceeds {max_mb} MB")
    if not data:
        raise UploadRejected("empty file")
    ext = Path(filename).suffix.lower()
    ctype = EXT_TYPES.get(ext)
    if not ctype:
        raise UploadRejected(f"file type {ext or '(none)'} not allowed")
    if any(data.startswith(m) for m in BLOCKED_MAGIC):
        raise UploadRejected("executable content is not allowed")
    magic = ALLOWED_TYPES.get(ctype)
    if magic and not data.startswith(magic):
        raise UploadRejected("file content does not match its extension")
    if ctype.startswith("text/") or ctype == "application/json":
        try:
            data[:4096].decode("utf-8")
        except UnicodeDecodeError as e:
            raise UploadRejected("text file is not valid UTF-8") from e
    if ext in (".docx", ".xlsx"):
        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                total = sum(i.file_size for i in z.infolist())
        except zipfile.BadZipFile as e:
            raise UploadRejected("corrupt office document") from e
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise UploadRejected("macro-enabled documents are not allowed")
        if total > 200 * 1024 * 1024:
            raise UploadRejected("archive expands too large (zip bomb protection)")
    return ctype


def _s3():
    s = get_settings()
    if s.storage_backend == "local":
        return None
    try:
        import boto3  # optional dependency, installed in the production image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("S3 storage is configured but boto3 is not installed") from exc
    kwargs = {}
    if s.s3_region:
        kwargs["region_name"] = s.s3_region
    if s.s3_endpoint:
        kwargs["endpoint_url"] = s.s3_endpoint
    # With no static pair, boto3 uses workload identity / instance roles / the normal AWS credential chain.
    if s.s3_access_key and s.s3_secret_key:
        kwargs["aws_access_key_id"] = s.s3_access_key
        kwargs["aws_secret_access_key"] = s.s3_secret_key
    return boto3.client("s3", **kwargs)


def _encryption_args() -> dict[str, str]:
    s = get_settings()
    if s.s3_kms_key_id:
        return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": s.s3_kms_key_id}
    return {"ServerSideEncryption": "AES256"}


def put(org_id: uuid.UUID, category: str, filename: str, data: bytes) -> tuple[str, str]:
    sha = hashlib.sha256(data).hexdigest()
    safe = "".join(c for c in Path(filename).name if c.isalnum() or c in "._-")[:120] or "file"
    key = f"{org_id}/{category}/{uuid.uuid4().hex}-{safe}"
    client = _s3()
    if client:
        client.put_object(Bucket=get_settings().s3_bucket, Key=key, Body=data, **_encryption_args())
    else:
        p = Path(get_settings().local_storage_dir) / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return key, sha


def get(org_id: uuid.UUID, key: str) -> bytes:
    if not key.startswith(f"{org_id}/"):
        raise PermissionError("storage key belongs to a different tenant")
    client = _s3()
    if client:
        return client.get_object(Bucket=get_settings().s3_bucket, Key=key)["Body"].read()
    return (Path(get_settings().local_storage_dir) / key).read_bytes()


def delete(org_id: uuid.UUID, key: str) -> None:
    if not key.startswith(f"{org_id}/"):
        raise PermissionError("storage key belongs to a different tenant")
    client = _s3()
    if client:
        client.delete_object(Bucket=get_settings().s3_bucket, Key=key)
    else:
        p = Path(get_settings().local_storage_dir) / key
        if p.exists():
            os.remove(p)


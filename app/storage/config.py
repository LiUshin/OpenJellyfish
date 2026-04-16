"""
Storage configuration — reads environment variables to determine backend mode.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class S3Config:
    bucket: str = ""
    region: str = "us-east-1"
    endpoint_url: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    prefix: str = ""


_STORAGE_BACKEND: Optional[str] = None
_S3_CONFIG: Optional[S3Config] = None


def get_storage_backend() -> str:
    """Return 'local' or 's3'. Default is 'local'."""
    global _STORAGE_BACKEND
    if _STORAGE_BACKEND is None:
        _STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").lower().strip()
        if _STORAGE_BACKEND not in ("local", "s3"):
            _STORAGE_BACKEND = "local"
    return _STORAGE_BACKEND


def is_s3_mode() -> bool:
    return get_storage_backend() == "s3"


def get_s3_config() -> S3Config:
    global _S3_CONFIG
    if _S3_CONFIG is None:
        endpoint = os.environ.get("S3_ENDPOINT_URL", "").strip() or None
        _S3_CONFIG = S3Config(
            bucket=os.environ.get("S3_BUCKET", ""),
            region=os.environ.get("S3_REGION", "us-east-1"),
            endpoint_url=endpoint,
            access_key_id=os.environ.get("S3_ACCESS_KEY_ID", "").strip() or None,
            secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY", "").strip() or None,
            prefix=os.environ.get("S3_PREFIX", "").strip().rstrip("/"),
        )
    return _S3_CONFIG

"""
Storage configuration — reads environment variables to determine backend mode.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


R2_HOST_SUFFIX = "r2.cloudflarestorage.com"


@dataclass
class S3Config:
    bucket: str = ""
    region: str = "us-east-1"
    endpoint_url: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    prefix: str = ""
    # "when_supported" (boto3 default) | "when_required"
    checksum_mode: str = "when_supported"
    # None (boto3 auto) | "path" | "virtual"
    addressing_style: Optional[str] = None


def _resolve_region(raw: str, endpoint: Optional[str]) -> str:
    if raw:
        return raw
    # R2 only accepts the pseudo-region "auto"; a wrong one fails signature
    # verification with an error that says nothing about the region.
    if endpoint and R2_HOST_SUFFIX in endpoint:
        return "auto"
    return "us-east-1"


def _resolve_checksum_mode(raw: str, endpoint: Optional[str]) -> str:
    """boto3 >= 1.36 sends CRC32 integrity headers on every upload. Real S3
    wants them; several S3-compatible implementations reject the request
    outright. Default to the conservative setting whenever a custom endpoint
    is configured, since that means we are not talking to AWS."""
    raw = (raw or "auto").lower()
    if raw in ("when_supported", "when_required"):
        return raw
    return "when_required" if endpoint else "when_supported"


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


def sandbox_budget_bytes() -> int:
    """Cap on how much of docs/ + scripts/ is materialized for one script run.

    Applies to both backends: it bounds network transfer on S3 and bounds the
    per-run copy/hardlink cost on local.
    """
    raw = os.environ.get("SANDBOX_MAX_MB", "").strip()
    try:
        mb = int(raw, 10) if raw else 512
    except ValueError:
        mb = 512
    return mb * 1024 * 1024


def get_s3_config() -> S3Config:
    global _S3_CONFIG
    if _S3_CONFIG is None:
        endpoint = os.environ.get("S3_ENDPOINT_URL", "").strip() or None
        style = os.environ.get("S3_ADDRESSING_STYLE", "").strip().lower() or None
        _S3_CONFIG = S3Config(
            bucket=os.environ.get("S3_BUCKET", ""),
            region=_resolve_region(os.environ.get("S3_REGION", "").strip(), endpoint),
            endpoint_url=endpoint,
            access_key_id=os.environ.get("S3_ACCESS_KEY_ID", "").strip() or None,
            secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY", "").strip() or None,
            prefix=os.environ.get("S3_PREFIX", "").strip().rstrip("/"),
            checksum_mode=_resolve_checksum_mode(
                os.environ.get("S3_CHECKSUM_MODE", "").strip(), endpoint,
            ),
            addressing_style=style if style in ("path", "virtual") else None,
        )
    return _S3_CONFIG

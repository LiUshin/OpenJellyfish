"""
Storage abstraction layer — factory functions for backend selection.

Usage:
    from app.storage import get_storage_service, create_agent_backend

    storage = get_storage_service()          # StorageService (for REST API / tools)
    backend = create_agent_backend(user_id)  # BackendProtocol (for deepagents)

IMPORTANT: Code outside this package must NEVER import from app.storage.config
or check is_s3_mode(). All backend differences are handled inside this package.
"""

from typing import Any

from app.storage.config import get_storage_backend, is_s3_mode, get_s3_config
from app.storage.base import StorageService

_storage_service: StorageService | None = None


def get_storage_service() -> StorageService:
    """Return a singleton StorageService matching STORAGE_BACKEND env var."""
    global _storage_service
    if _storage_service is None:
        if is_s3_mode():
            from app.storage.s3 import S3StorageService
            _storage_service = S3StorageService()
        else:
            from app.storage.local import LocalStorageService
            _storage_service = LocalStorageService()
    return _storage_service


def create_agent_backend(root_dir: str, *, user_id: str | None = None) -> Any:
    """Create a deepagents BackendProtocol instance.

    - local mode: returns FilesystemBackend(root_dir=root_dir, virtual_mode=True)
    - S3 mode:    returns S3Backend(bucket=..., prefix=<user-scoped>)
    """
    if is_s3_mode():
        from app.storage.s3_backend import S3Backend
        cfg = get_s3_config()
        prefix_parts = [p for p in [cfg.prefix, user_id or "", "fs"] if p]
        return S3Backend(bucket=cfg.bucket, prefix="/".join(prefix_parts))
    else:
        from deepagents.backends.filesystem import FilesystemBackend
        return FilesystemBackend(root_dir=root_dir, virtual_mode=True)


def create_consumer_backend(
    admin_id: str, service_id: str, conv_id: str, gen_dir: str,
) -> Any:
    """Create a deepagents BackendProtocol for consumer conversation generated files."""
    if is_s3_mode():
        from app.storage.s3_backend import S3Backend
        cfg = get_s3_config()
        prefix_parts = [p for p in [cfg.prefix, admin_id, "svc", service_id, conv_id, "gen"] if p]
        return S3Backend(bucket=cfg.bucket, prefix="/".join(prefix_parts))
    else:
        from deepagents.backends.filesystem import FilesystemBackend
        return FilesystemBackend(root_dir=gen_dir, virtual_mode=True)

"""
Abstract base class for storage services.

ALL file I/O outside of app/storage/ MUST go through this interface.
Business logic must never check is_s3_mode() — the backend is transparent.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator, List, Optional


@dataclass
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified_at: str = ""


class StorageService(ABC):
    """Unified file-operation interface consumed by REST routes, AI tools, batch, etc."""

    # ── directory listing ──

    @abstractmethod
    def list_dir(self, user_id: str, path: str = "/") -> List[FileEntry]:
        ...

    # ── read ──

    @abstractmethod
    def read_text(self, user_id: str, path: str) -> str:
        ...

    @abstractmethod
    def read_bytes(self, user_id: str, path: str) -> bytes:
        ...

    # ── write ──

    @abstractmethod
    def write_text(self, user_id: str, path: str, content: str) -> None:
        ...

    @abstractmethod
    def write_bytes(self, user_id: str, path: str, data: bytes) -> None:
        ...

    # ── edit ──

    @abstractmethod
    def edit_text(self, user_id: str, path: str, old_string: str, new_string: str) -> None:
        ...

    # ── delete / move ──

    @abstractmethod
    def delete(self, user_id: str, path: str) -> None:
        ...

    @abstractmethod
    def move(self, user_id: str, source: str, destination: str) -> str:
        ...

    @abstractmethod
    def copy(self, user_id: str, source: str, destination: str) -> str:
        """Recursively copy a file or directory from source to destination.

        - If destination resolves to an existing directory, the basename of
          source is appended (mimicking shutil/cp -r semantics).
        - If destination already exists (after the directory expansion),
          raises FileExistsError (caller should rename and retry).
        - Returns the resulting absolute (storage-relative) path of the copy.
        """
        ...

    @abstractmethod
    def walk_files(
        self, user_id: str, path: str,
    ) -> Generator[tuple[str, bytes], None, None]:
        """Yield (rel_path_under_path, bytes_content) for every file under
        `path` (recursive). For a single file, yields one tuple where rel_path
        is the basename. Used by zip streaming.

        rel_path uses '/' separators and never starts with '/'.
        """
        ...

    # ── queries ──

    @abstractmethod
    def exists(self, user_id: str, path: str) -> bool:
        ...

    @abstractmethod
    def is_file(self, user_id: str, path: str) -> bool:
        ...

    @abstractmethod
    def is_dir(self, user_id: str, path: str) -> bool:
        ...

    @abstractmethod
    def makedirs(self, user_id: str, path: str) -> None:
        ...

    # ── user init ──

    @abstractmethod
    def ensure_user_dirs(self, user_id: str) -> None:
        """Create the full initial directory structure for a new user,
        including docs/, scripts/, generated/images|audio|videos/."""
        ...

    # ── HTTP responses (concrete — delegates to abstract helpers) ──

    @abstractmethod
    def _get_media_url(self, user_id: str, path: str, expires_in: int = 3600) -> Optional[str]:
        """Return presigned URL (S3) or None (local)."""
        ...

    @abstractmethod
    def _get_real_path(self, user_id: str, path: str) -> str:
        """Return absolute local path. Only valid in local mode."""
        ...

    def file_response(
        self, user_id: str, path: str, *,
        media_type: Optional[str] = None,
        inline: bool = True,
        filename: Optional[str] = None,
    ) -> Any:
        """Return a FastAPI Response for serving a file.

        - local mode  → FileResponse
        - S3 mode     → 302 redirect to presigned URL
        """
        from fastapi.responses import FileResponse, RedirectResponse

        url = self._get_media_url(user_id, path)
        if url is not None:
            return RedirectResponse(url=url)
        full = self._get_real_path(user_id, path)
        headers = {}
        if inline:
            headers["Cache-Control"] = "private, max-age=3600"
            headers["Content-Disposition"] = "inline"
        return FileResponse(
            full, media_type=media_type,
            filename=filename, headers=headers if inline else None,
        )

    # ── consumer (service) operations ──

    @abstractmethod
    def list_consumer_files(
        self, admin_id: str, service_id: str, conv_id: str,
    ) -> List[dict]:
        """Return [{"path": rel, "size": int}, ...]"""
        ...

    @abstractmethod
    def read_consumer_bytes(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> bytes:
        ...

    @abstractmethod
    def write_consumer_bytes(
        self, admin_id: str, service_id: str, conv_id: str, path: str, data: bytes,
    ) -> None:
        ...

    @abstractmethod
    def consumer_exists(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> bool:
        ...

    @abstractmethod
    def _get_consumer_media_url(
        self, admin_id: str, service_id: str, conv_id: str,
        path: str, expires_in: int = 3600,
    ) -> Optional[str]:
        ...

    @abstractmethod
    def _get_consumer_real_path(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> str:
        ...

    def consumer_file_response(
        self, admin_id: str, service_id: str, conv_id: str, path: str,
    ) -> Any:
        """Return a FastAPI Response for serving a consumer generated file."""
        from fastapi.responses import FileResponse, RedirectResponse

        url = self._get_consumer_media_url(admin_id, service_id, conv_id, path)
        if url is not None:
            return RedirectResponse(url=url)
        full = self._get_consumer_real_path(admin_id, service_id, conv_id, path)
        return FileResponse(full)

    # ── script execution ──

    @abstractmethod
    @contextmanager
    def script_execution(
        self, user_id: str, script_path: str,
    ) -> Generator[dict, None, None]:
        """Context manager providing local dirs for script execution.

        Yields dict with keys:
            scripts_dir  — local dir containing the script file
            docs_dir     — local dir for read-only docs
            write_dirs   — list of local dirs the script may write to

        In S3 mode: downloads the script to temp, and uploads generated/
        results back to S3 on exit.
        """
        ...

    @abstractmethod
    @contextmanager
    def consumer_script_execution(
        self, admin_id: str, service_id: str, conv_id: str, script_path: str,
    ) -> Generator[dict, None, None]:
        """Like script_execution, but writes go to the consumer conversation dir.

        Scripts and docs are read from the admin's filesystem (read-only).
        Generated output is written to the consumer conversation's generated/ dir.
        """
        ...

"""LockAwareBackend — a transparent wrapper enforcing workspace write locks.

Wraps any deepagents BackendProtocol implementation (FilesystemBackend / S3Backend)
and intercepts the mutating operations (write / edit / upload). Reads are always
delegated untouched (readers never block).

Design constraints (verified against the installed deepagents):
  * `FilesystemMiddleware._get_backend` does `if callable(self.backend): factory`.
    => this wrapper MUST NOT be callable (plain class, no __call__), otherwise it
    would be mistaken for a BackendFactory and invoked with the runtime.
  * `_supports_execution` uses `isinstance(backend, SandboxBackendProtocol)`; a
    plain wrapper is not a subclass => no `execute` tool is added (correct; our
    backends are not sandboxes).
  * The middleware calls `backend.write/awrite/edit/aedit` and expects
    WriteResult/EditResult with an `error` field for recoverable failures — so we
    signal a locked write by returning `WriteResult(error=...)` (surfaced to the
    LLM as a normal, actionable tool error).

Everything not explicitly overridden is delegated to the inner backend via
`__getattr__` (ls/read/glob/grep/download + async variants + attributes like cwd).
"""

from __future__ import annotations

from app.services import workspace_lock as wl


def _upload_path(item) -> str:
    """Extract the destination path from an upload item (tuple or dict form)."""
    if isinstance(item, (list, tuple)) and item:
        return item[0]
    if isinstance(item, dict):
        return item.get("path") or item.get("name") or "/"
    return "/"


class LockAwareBackend:
    """Transparent write-lock enforcement wrapper around a deepagents backend."""

    def __init__(self, inner):
        # Use object.__setattr__ so __getattr__ delegation stays clean.
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        # Only reached for attributes not defined on this wrapper => delegate.
        return getattr(object.__getattribute__(self, "_inner"), name)

    # ── write ──

    def write(self, file_path: str, content: str):
        from deepagents.backends.protocol import WriteResult
        msg = wl.check_write(file_path)
        if msg:
            return WriteResult(error=msg)
        return self._inner.write(file_path, content)

    async def awrite(self, file_path: str, content: str):
        from deepagents.backends.protocol import WriteResult
        msg = wl.check_write(file_path)
        if msg:
            return WriteResult(error=msg)
        return await self._inner.awrite(file_path, content)

    # ── edit ──

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        from deepagents.backends.protocol import EditResult
        msg = wl.check_write(file_path)
        if msg:
            return EditResult(error=msg)
        return self._inner.edit(file_path, old_string, new_string, replace_all)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        from deepagents.backends.protocol import EditResult
        msg = wl.check_write(file_path)
        if msg:
            return EditResult(error=msg)
        return await self._inner.aedit(file_path, old_string, new_string, replace_all)

    # ── upload (defensive: not usually exposed to the LLM) ──

    def upload_files(self, files):
        from deepagents.backends.protocol import FileUploadResponse
        responses = []
        for item in files:
            path = _upload_path(item)
            if wl.check_write(path):
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
            else:
                responses.extend(self._inner.upload_files([item]))
        return responses

    async def aupload_files(self, files):
        from deepagents.backends.protocol import FileUploadResponse
        responses = []
        for item in files:
            path = _upload_path(item)
            if wl.check_write(path):
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
            else:
                responses.extend(await self._inner.aupload_files([item]))
        return responses

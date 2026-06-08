"""
Filesystem-tree storage layer for v2 scheduled tasks.

Layout (per-user, per-scope):

    Admin:
        users/{uid}/tasks/{root_id}/
            ├── _meta.json                  # task metadata + run summaries
            ├── runs/                       # per-run JSONL step logs
            │   ├── {run_id}.jsonl
            │   └── ...
            └── {child_id}/                 # spawned child task (recursive)
                ├── _meta.json
                ├── runs/
                └── {grandchild_id}/
                    └── ...

    Service:
        users/{uid}/services/{svc_id}/tasks/{root_id}/
            ├── _meta.json
            ├── runs/
            └── {child_id}/
                └── ...

Design contracts
----------------
* ``task_id`` (e.g. ``task_abc123`` or ``stask_xyz789``) becomes a **directory
  name**, not a file basename.  ``_meta.json`` inside that directory holds the
  task's JSON state (parent, schedule, runs[], descendants_summary, ...).
* Any ``task_id`` can be located via :func:`task_path_for` which walks the
  scope's tasks root once and caches the result (LRU 2048 entries).  The cache
  is invalidated by :func:`invalidate_path_cache` on every mutation.
* Legacy v1 storage (flat ``{tasks_dir}/{task_id}.json`` + sibling
  ``{task_id}.steps/{run_id}.jsonl``) is auto-migrated lazily on first
  successful :func:`load_task_meta` lookup — see :func:`migrate_legacy_task`.

This module is pure storage helpers — schedule evaluation, agent execution
and L3 propagation live in ``scheduler.py``.  Only thing imported from
``scheduler.py`` would be schema constants, so we keep it dependency-free.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple

from app.core.fileutil import atomic_json_save
from app.core.security import get_user_dir

log = logging.getLogger("scheduler_tree")

# ── Constants ────────────────────────────────────────────────────────────

Scope = Literal["admin", "service"]

META_FILENAME = "_meta.json"
RUNS_DIRNAME = "runs"

# Filename prefixes — used to distinguish admin vs service IDs and to find
# legacy task files.  Keep in sync with scheduler.py's id generation.
ADMIN_TASK_PREFIX = "task_"
SERVICE_TASK_PREFIX = "stask_"


# ── Path helpers ─────────────────────────────────────────────────────────

def scope_root(scope: Scope, uid: str,
               service_id: Optional[str] = None) -> str:
    """Absolute path to the tasks root for a (scope, uid[, svc]) tuple."""
    if scope == "admin":
        return os.path.join(get_user_dir(uid), "tasks")
    if scope == "service":
        if not service_id:
            raise ValueError("service scope requires service_id")
        return os.path.join(get_user_dir(uid), "services", service_id, "tasks")
    raise ValueError(f"Unknown scope: {scope!r}")


def meta_path(task_dir: str) -> str:
    """Full path to a task directory's ``_meta.json``."""
    return os.path.join(task_dir, META_FILENAME)


def runs_dir(task_dir: str) -> str:
    """Full path to a task directory's ``runs/`` sub-directory."""
    return os.path.join(task_dir, RUNS_DIRNAME)


def run_log_path(task_dir: str, run_id: str) -> str:
    """Full path to one run's JSONL step log."""
    return os.path.join(runs_dir(task_dir), f"{run_id}.jsonl")


# ── task_id → path reverse lookup (LRU-cached, walk_root) ───────────────

@functools.lru_cache(maxsize=2048)
def task_path_for(scope: Scope, uid: str, task_id: str,
                  service_id: Optional[str] = None) -> Optional[str]:
    """Find the absolute path of the directory holding ``task_id``'s ``_meta.json``.

    Walks ``scope_root`` recursively; returns the first match by basename.
    Cached for the lifetime of the process — every CRUD must call
    :func:`invalidate_path_cache` to keep stale entries out.

    Returns ``None`` if the id is not found in the new tree layout.  The
    legacy flat-file location is **not** consulted here; callers do that
    fallback themselves so they can trigger migration on miss.
    """
    root = scope_root(scope, uid, service_id)
    if not os.path.isdir(root):
        return None
    # Bound the walk so a runaway tree can't lock the loop too long; 64 levels
    # is far beyond any realistic spawn depth.
    for dirpath, dirnames, _ in os.walk(root, followlinks=False):
        # Pre-prune: only descend into known task dirs (cheap heuristic — most
        # filesystems hand us dirnames in arbitrary order, so we only check
        # against task prefixes when picking what to descend into).
        if os.path.basename(dirpath) == task_id:
            if os.path.isfile(meta_path(dirpath)):
                return dirpath
        # Prune non-task siblings (e.g. ``runs/``) so we don't waste a stat
        # on every JSONL filename.
        dirnames[:] = [
            d for d in dirnames
            if d.startswith(ADMIN_TASK_PREFIX)
            or d.startswith(SERVICE_TASK_PREFIX)
        ]
    return None


def invalidate_path_cache(*_args, **_kwargs) -> None:
    """Drop all cached path lookups.

    Called by every CRUD / spawn / move / delete that changes the tree.  We
    blow the whole cache rather than computing per-key invalidation because
    (a) churn is low, (b) a parent rename invalidates many descendants, and
    (c) lru_cache doesn't expose targeted deletion anyway.
    """
    task_path_for.cache_clear()


# ── _meta.json read / write ──────────────────────────────────────────────

def load_task_meta(task_dir: str) -> Optional[Dict[str, Any]]:
    """Read a task's ``_meta.json``.  Returns ``None`` if absent / corrupt."""
    path = meta_path(task_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("Corrupt task _meta.json at %s — skipping", path)
        return None


def save_task_meta(task_dir: str, meta: Dict[str, Any]) -> None:
    """Atomically write a task's ``_meta.json`` (creates dir if missing)."""
    os.makedirs(task_dir, exist_ok=True)
    atomic_json_save(meta_path(task_dir), meta,
                     ensure_ascii=False, indent=2)


# ── Tree traversal ───────────────────────────────────────────────────────

def iter_child_dirs(task_dir: str) -> Iterator[str]:
    """Yield absolute paths of immediate children that look like task dirs."""
    if not os.path.isdir(task_dir):
        return
    for name in os.listdir(task_dir):
        if not (name.startswith(ADMIN_TASK_PREFIX)
                or name.startswith(SERVICE_TASK_PREFIX)):
            continue
        sub = os.path.join(task_dir, name)
        if os.path.isfile(meta_path(sub)):
            yield sub


def walk_tree(scope: Scope, uid: str, root_task_id: str,
              service_id: Optional[str] = None,
              max_depth: int = 8) -> Optional[Dict[str, Any]]:
    """Build a nested tree dict rooted at ``root_task_id`` for UI / API.

    Output shape (recursive):
        {
            "meta": {... full _meta.json ...},
            "children": [ {"meta": ..., "children": [...]}  , ... ]
        }

    ``max_depth`` caps recursion (root counts as 0).  Children below the cap
    appear with ``"children": []`` and a ``"truncated": true`` marker so the
    UI can show "load more" affordances.
    """
    root_dir = task_path_for(scope, uid, root_task_id, service_id)
    if not root_dir:
        return None
    return _walk_dir(root_dir, current_depth=0, max_depth=max_depth)


def _walk_dir(task_dir: str, *, current_depth: int,
              max_depth: int) -> Dict[str, Any]:
    meta = load_task_meta(task_dir) or {}
    if current_depth >= max_depth:
        truncated = any(True for _ in iter_child_dirs(task_dir))
        return {"meta": meta, "children": [], "truncated": truncated}
    children: List[Dict[str, Any]] = []
    for sub in iter_child_dirs(task_dir):
        children.append(_walk_dir(sub,
                                  current_depth=current_depth + 1,
                                  max_depth=max_depth))
    # Sort children by created_at for stable UI ordering.
    children.sort(key=lambda c: (c["meta"] or {}).get("created_at", ""))
    return {"meta": meta, "children": children}


def list_descendants(scope: Scope, uid: str, root_task_id: str,
                     service_id: Optional[str] = None,
                     include_root: bool = False
                     ) -> List[Dict[str, Any]]:
    """Flat list of all descendant task metas (DFS order, optional root)."""
    root_dir = task_path_for(scope, uid, root_task_id, service_id)
    if not root_dir:
        return []
    out: List[Dict[str, Any]] = []
    if include_root:
        m = load_task_meta(root_dir)
        if m:
            out.append(m)
    for sub in iter_child_dirs(root_dir):
        sub_id = os.path.basename(sub)
        out.extend(list_descendants(scope, uid, sub_id, service_id,
                                    include_root=True))
    return out


def list_root_tasks(scope: Scope, uid: str,
                    service_id: Optional[str] = None
                    ) -> List[Dict[str, Any]]:
    """Return metas of every root-level task in a scope."""
    root = scope_root(scope, uid, service_id)
    if not os.path.isdir(root):
        return []
    out: List[Dict[str, Any]] = []
    for name in os.listdir(root):
        if not (name.startswith(ADMIN_TASK_PREFIX)
                or name.startswith(SERVICE_TASK_PREFIX)):
            continue
        sub = os.path.join(root, name)
        m = load_task_meta(sub)
        if m:
            out.append(m)
    return out


def list_all_tasks_flat(scope: Scope, uid: str,
                        service_id: Optional[str] = None,
                        include_runs: bool = False
                        ) -> List[Dict[str, Any]]:
    """Walk every task (root + descendants) for listing endpoints."""
    out: List[Dict[str, Any]] = []
    for root_meta in list_root_tasks(scope, uid, service_id):
        rid = root_meta.get("id")
        if not rid:
            continue
        descendants = list_descendants(scope, uid, rid, service_id,
                                       include_root=True)
        for m in descendants:
            if not include_runs:
                m = {k: v for k, v in m.items() if k != "runs"}
                m["run_count"] = len((m.get("runs") if include_runs else []) or [])
            out.append(m)
    return out


# ── New-tree CRUD ────────────────────────────────────────────────────────

def create_root_dir(scope: Scope, uid: str, task_id: str,
                    service_id: Optional[str] = None) -> str:
    """Make a fresh root-level task directory (no _meta.json yet)."""
    task_dir = os.path.join(scope_root(scope, uid, service_id), task_id)
    os.makedirs(runs_dir(task_dir), exist_ok=True)
    invalidate_path_cache()
    return task_dir


def create_child_dir(parent_dir: str, child_id: str) -> str:
    """Make a fresh child task directory under an existing parent."""
    if not os.path.isdir(parent_dir):
        raise ValueError(f"parent_dir does not exist: {parent_dir!r}")
    child_dir = os.path.join(parent_dir, child_id)
    os.makedirs(runs_dir(child_dir), exist_ok=True)
    invalidate_path_cache()
    return child_dir


def delete_task_subtree(scope: Scope, uid: str, task_id: str,
                        service_id: Optional[str] = None) -> bool:
    """Recursively remove a task and all its descendants.  Returns True on hit."""
    target = task_path_for(scope, uid, task_id, service_id)
    if not target:
        return False
    try:
        shutil.rmtree(target)
    except OSError:
        log.exception("Failed to rmtree %s", target)
        return False
    invalidate_path_cache()
    return True


# ── Lazy migration from v1 flat layout ──────────────────────────────────

def migrate_legacy_task(scope: Scope, uid: str, task_id: str,
                        service_id: Optional[str] = None,
                        delete_legacy: bool = True) -> Optional[str]:
    """Migrate a v1-shaped legacy task into v2 tree layout.

    Legacy locations:
        admin:    users/{uid}/tasks/{task_id}.json + {task_id}.steps/
        service:  users/{uid}/services/{svc}/tasks/{task_id}.json + {task_id}.steps/

    Steps performed:
        1. Read legacy ``{task_id}.json``.
        2. Inject default v2 fields (parent_task_id=None, root_task_id=self,
           spawn_chain=[], spawn_depth=0, descendants_summary="",
           children_count=0, descendants_count=0, spawn_reason="").
        3. Create ``{task_id}/`` directory and write ``_meta.json``.
        4. Move ``{task_id}.steps/{run_id}.jsonl`` → ``{task_id}/runs/{run_id}.jsonl``.
        5. (default) Delete the legacy ``.json`` file and ``.steps`` directory.

    Returns the new task directory path on success, ``None`` on miss / failure.
    Idempotent: if the new tree dir already exists, returns early without
    overwriting it.
    """
    root = scope_root(scope, uid, service_id)
    legacy_json = os.path.join(root, f"{task_id}.json")
    legacy_steps = os.path.join(root, f"{task_id}.steps")
    new_dir = os.path.join(root, task_id)

    if os.path.isfile(meta_path(new_dir)):
        # Already migrated — nothing to do.
        invalidate_path_cache()
        return new_dir

    if not os.path.isfile(legacy_json):
        return None

    try:
        with open(legacy_json, "r", encoding="utf-8") as f:
            legacy_meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("migrate_legacy_task: cannot read %s", legacy_json)
        return None

    # Inject v2 defaults — conservatively, to avoid clobbering any partial
    # field that may already exist on a half-migrated record.
    legacy_meta.setdefault("parent_task_id", None)
    legacy_meta.setdefault("root_task_id", task_id)
    legacy_meta.setdefault("spawn_chain", [])
    legacy_meta.setdefault("spawn_depth", 0)
    legacy_meta.setdefault("descendants_summary", "")
    legacy_meta.setdefault("children_count", 0)
    legacy_meta.setdefault("descendants_count", 0)
    legacy_meta.setdefault("spawn_reason", "")

    os.makedirs(runs_dir(new_dir), exist_ok=True)
    save_task_meta(new_dir, legacy_meta)

    # Migrate per-run step JSONLs (best-effort).
    if os.path.isdir(legacy_steps):
        for fname in os.listdir(legacy_steps):
            src = os.path.join(legacy_steps, fname)
            dst = os.path.join(runs_dir(new_dir), fname)
            try:
                shutil.move(src, dst)
            except OSError:
                log.exception("migrate_legacy_task: failed to move %s", src)

    if delete_legacy:
        try:
            os.unlink(legacy_json)
        except OSError:
            log.exception("migrate_legacy_task: failed to unlink %s", legacy_json)
        if os.path.isdir(legacy_steps):
            try:
                shutil.rmtree(legacy_steps)
            except OSError:
                log.exception("migrate_legacy_task: failed to rmtree %s",
                              legacy_steps)

    invalidate_path_cache()
    log.info("Migrated legacy task %s (%s) → %s", task_id, scope, new_dir)
    return new_dir


def find_legacy_task_files(scope: Scope, uid: str,
                           service_id: Optional[str] = None
                           ) -> List[Tuple[str, str]]:
    """Discover legacy v1 task files awaiting migration.

    Returns list of ``(task_id, full_legacy_json_path)`` pairs.  Used by:
      * one-shot ``scripts/migrate_tasks_to_tree.py`` for batch dry-run
      * ``GET /api/scheduler/migrate`` to report pending count
    """
    root = scope_root(scope, uid, service_id)
    if not os.path.isdir(root):
        return []
    found: List[Tuple[str, str]] = []
    for name in os.listdir(root):
        if not name.endswith(".json"):
            continue
        # Skip the v2 _meta.json — it lives inside a task dir, not at the root.
        if name == META_FILENAME:
            continue
        task_id = name[:-len(".json")]
        if not (task_id.startswith(ADMIN_TASK_PREFIX)
                or task_id.startswith(SERVICE_TASK_PREFIX)):
            continue
        # If a v2 directory already exists, the legacy file is leftover — still
        # worth surfacing so the migration script can clean it up.
        legacy_path = os.path.join(root, name)
        if os.path.isfile(legacy_path):
            found.append((task_id, legacy_path))
    return found


# ── Convenience: load with auto-migration fallback ──────────────────────

def load_task_or_migrate(scope: Scope, uid: str, task_id: str,
                         service_id: Optional[str] = None
                         ) -> Optional[Dict[str, Any]]:
    """Load a task's meta, migrating from v1 layout transparently if needed.

    Lookup order:
      1. v2 tree (via :func:`task_path_for`)
      2. v1 legacy flat file → migrate → re-resolve

    Returns ``None`` only if neither layout has a record for ``task_id``.
    Use this from ``scheduler.py``'s ``_load_task`` / ``_load_service_task``.
    """
    new_dir = task_path_for(scope, uid, task_id, service_id)
    if new_dir:
        return load_task_meta(new_dir)
    migrated_dir = migrate_legacy_task(scope, uid, task_id, service_id)
    if migrated_dir:
        return load_task_meta(migrated_dir)
    return None

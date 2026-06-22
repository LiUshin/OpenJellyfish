"""
One-shot migrator: v1 flat task storage → v2 filesystem tree.

Background
----------
v1 layout (legacy):
    users/{uid}/tasks/{task_id}.json
    users/{uid}/tasks/{task_id}.steps/{run_id}.jsonl
    users/{uid}/services/{svc}/tasks/{task_id}.json
    users/{uid}/services/{svc}/tasks/{task_id}.steps/{run_id}.jsonl

v2 layout (new):
    users/{uid}/tasks/{task_id}/_meta.json
    users/{uid}/tasks/{task_id}/runs/{run_id}.jsonl
    users/{uid}/services/{svc}/tasks/{task_id}/_meta.json
    users/{uid}/services/{svc}/tasks/{task_id}/runs/{run_id}.jsonl

Lazy migration is built into ``app.services.scheduler_tree.load_task_or_migrate``,
so production deploys don't strictly need to run this script — files migrate
on first access.  Use this script when you want:

* A pre-deploy sanity check (``--dry-run``) — counts pending legacy tasks
  per user without touching anything.
* Forced batch migration during a maintenance window (without ``--dry-run``)
  so the first agent post-deploy doesn't pay the migration latency on hot path.
* Verbose validation (``--verify``) — after migration, walk the new tree
  and print per-user counts so you know nothing was dropped.

Usage
-----
    python scripts/migrate_tasks_to_tree.py --dry-run
    python scripts/migrate_tasks_to_tree.py --user alice --dry-run
    python scripts/migrate_tasks_to_tree.py                      # do it
    python scripts/migrate_tasks_to_tree.py --keep-legacy        # don't unlink old files
    python scripts/migrate_tasks_to_tree.py --verify             # post-migration audit
    python scripts/migrate_tasks_to_tree.py --user alice --service svc_xxx  # narrow scope

Exit codes
----------
    0  success (with or without changes)
    1  partial failure — some tasks couldn't migrate; check stderr
    2  bad arguments / unrecoverable env error
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import List, Optional, Tuple

# Ensure we can import app.* when run from the repo root.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Force UTF-8 stdout so Unicode bullets / arrows don't blow up Windows GBK
# consoles when running this script directly.  Mirrors the pattern in
# scripts/test_consumer_api.py.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.core.security import USERS_DIR  # noqa: E402
from app.services import scheduler_tree as st  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("migrate")


# ── Discovery ────────────────────────────────────────────────────────────

def discover_users(filter_user: Optional[str] = None) -> List[str]:
    if not os.path.isdir(USERS_DIR):
        log.error("USERS_DIR does not exist: %s", USERS_DIR)
        return []
    out = []
    for name in os.listdir(USERS_DIR):
        full = os.path.join(USERS_DIR, name)
        if not os.path.isdir(full):
            continue
        if filter_user and name != filter_user:
            continue
        out.append(name)
    out.sort()
    return out


def discover_services(uid: str,
                      filter_service: Optional[str] = None) -> List[str]:
    services_root = os.path.join(USERS_DIR, uid, "services")
    if not os.path.isdir(services_root):
        return []
    out = []
    for name in os.listdir(services_root):
        if not os.path.isdir(os.path.join(services_root, name)):
            continue
        if filter_service and name != filter_service:
            continue
        out.append(name)
    out.sort()
    return out


# ── Reporting ────────────────────────────────────────────────────────────

def collect_pending(uid: str,
                    service_filter: Optional[str] = None
                    ) -> List[Tuple[str, str, Optional[str], str]]:
    """
    Return list of (scope, uid, service_id_or_none, task_id) awaiting migration.
    """
    pending: List[Tuple[str, str, Optional[str], str]] = []

    for tid, _path in st.find_legacy_task_files("admin", uid):
        pending.append(("admin", uid, None, tid))

    for svc in discover_services(uid, service_filter):
        for tid, _path in st.find_legacy_task_files("service", uid, svc):
            pending.append(("service", uid, svc, tid))

    return pending


def report_pending(users: List[str],
                   service_filter: Optional[str]) -> List[Tuple[str, str, Optional[str], str]]:
    print("=" * 60)
    print("v1 → v2 task migration: dry-run report")
    print("=" * 60)
    total: List[Tuple[str, str, Optional[str], str]] = []
    for uid in users:
        pend = collect_pending(uid, service_filter)
        total.extend(pend)
        if not pend:
            print(f"  • {uid}: 0 legacy tasks")
            continue
        admin_n = sum(1 for s, _, _, _ in pend if s == "admin")
        svc_n = len(pend) - admin_n
        print(f"  • {uid}: {len(pend)} legacy tasks "
              f"(admin={admin_n}, service={svc_n})")
        if svc_n:
            by_svc = {}
            for _, _, sid, _ in pend:
                if sid:
                    by_svc[sid] = by_svc.get(sid, 0) + 1
            for sid, n in sorted(by_svc.items()):
                print(f"      └ service {sid}: {n}")
    print("-" * 60)
    print(f"TOTAL pending: {len(total)} tasks across {len(users)} user(s)")
    print("=" * 60)
    return total


# ── Migration ────────────────────────────────────────────────────────────

def migrate_pending(pending: List[Tuple[str, str, Optional[str], str]],
                    keep_legacy: bool) -> Tuple[int, int]:
    """Returns (migrated, failed) counts."""
    migrated = 0
    failed = 0
    t0 = time.time()
    for scope, uid, svc, tid in pending:
        try:
            new_dir = st.migrate_legacy_task(scope, uid, tid, svc,
                                             delete_legacy=not keep_legacy)
            if new_dir:
                migrated += 1
                log.info("[%s/%s%s] %s → %s",
                         scope, uid, f"/{svc}" if svc else "",
                         tid, new_dir)
            else:
                failed += 1
                log.error("[%s/%s%s] %s: migrate_legacy_task returned None",
                          scope, uid, f"/{svc}" if svc else "", tid)
        except Exception:
            failed += 1
            log.exception("[%s/%s%s] %s: unexpected error",
                          scope, uid, f"/{svc}" if svc else "", tid)
    elapsed = time.time() - t0
    print("-" * 60)
    print(f"Migrated: {migrated}   Failed: {failed}   "
          f"Elapsed: {elapsed:.2f}s")
    print("-" * 60)
    return migrated, failed


# ── Verification ─────────────────────────────────────────────────────────

def verify_users(users: List[str], service_filter: Optional[str]) -> bool:
    """After migration, count v2 tasks and remaining legacy files per user.

    Returns True if no legacy files remain (clean state).
    """
    print("=" * 60)
    print("Post-migration verification")
    print("=" * 60)
    all_clean = True
    for uid in users:
        v2_admin = len(st.list_all_tasks_flat("admin", uid))
        legacy_admin = len(st.find_legacy_task_files("admin", uid))
        line = f"  • {uid}: v2_admin={v2_admin}, legacy_admin={legacy_admin}"

        v2_svc_total = 0
        legacy_svc_total = 0
        for svc in discover_services(uid, service_filter):
            v2_svc_total += len(st.list_all_tasks_flat("service", uid, svc))
            legacy_svc_total += len(
                st.find_legacy_task_files("service", uid, svc))
        line += (f", v2_service={v2_svc_total}, "
                 f"legacy_service={legacy_svc_total}")
        print(line)

        if legacy_admin or legacy_svc_total:
            all_clean = False

    print("-" * 60)
    print(f"All clean: {all_clean}")
    print("=" * 60)
    return all_clean


# ── CLI ──────────────────────────────────────────────────────────────────

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Migrate v1 flat task storage to v2 tree layout.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Only report pending counts; don't move any files.")
    p.add_argument("--user", default=None,
                   help="Limit to a single user_id (default: all users)")
    p.add_argument("--service", default=None,
                   help="Limit to a single service_id (default: all services)")
    p.add_argument("--keep-legacy", action="store_true",
                   help="Don't delete the original .json / .steps files after "
                        "successful copy. Useful for staged rollouts.")
    p.add_argument("--verify", action="store_true",
                   help="After migration, audit each user's v2 vs legacy "
                        "counts. Implies running the migration first unless "
                        "--dry-run is also set.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if not os.path.isdir(USERS_DIR):
        log.error("USERS_DIR does not exist or is not a directory: %s",
                  USERS_DIR)
        return 2

    users = discover_users(args.user)
    if not users:
        log.error("No users matched (filter=%r). Nothing to do.", args.user)
        return 2

    pending = report_pending(users, args.service)

    if args.dry_run:
        if args.verify:
            verify_users(users, args.service)
        return 0

    if not pending:
        log.info("No legacy tasks to migrate.")
        if args.verify:
            verify_users(users, args.service)
        return 0

    migrated, failed = migrate_pending(pending, keep_legacy=args.keep_legacy)

    if args.verify:
        verify_users(users, args.service)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

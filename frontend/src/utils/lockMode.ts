/**
 * Workspace lock preference (per-browser, localStorage).
 *
 * lock_mode:
 *  - "auto"   (default): the backend grabs the broadest currently-free region so
 *             a solo session keeps full write access; concurrent sessions get the
 *             free complement. Zero friction.
 *  - "manual": lock exactly the paths the operator picked (lock_paths).
 *             Absolute paths like /docs/project/report.md or /scripts/foo/
 *             (same path-level semantics as Service allowed_docs/allowed_scripts).
 *  - "agent":  acquire nothing upfront; the agent declares its write region via
 *             the acquire_workspace tool when it first needs to write.
 */

export type LockMode = 'auto' | 'manual' | 'agent';

const MODE_KEY = 'ws_lock_mode';
const PATHS_KEY = 'ws_lock_paths';
export const LOCK_EVENT = 'ws-lock-mode-changed';

export function getLockMode(): LockMode {
  const v = localStorage.getItem(MODE_KEY);
  return v === 'manual' || v === 'agent' ? v : 'auto';
}

export function setLockMode(mode: LockMode): void {
  localStorage.setItem(MODE_KEY, mode);
  window.dispatchEvent(new Event(LOCK_EVENT));
}

export function getLockPaths(): string[] {
  try {
    const raw = localStorage.getItem(PATHS_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

export function setLockPaths(paths: string[]): void {
  localStorage.setItem(PATHS_KEY, JSON.stringify(paths));
  window.dispatchEvent(new Event(LOCK_EVENT));
}

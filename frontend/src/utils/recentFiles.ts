/**
 * Track the most-recently-opened file paths to prioritise them in the
 * chat input @-mention picker.
 *
 * - Storage: localStorage `jf-recent-files` (array of paths, most-recent-first).
 * - Hook points: `fileWorkspaceContext.openFile` (any open in FilePanel) and
 *   the picker itself when the user picks a candidate.
 * - Cap: 30 paths. Older entries fall off; we only need a small set so the
 *   "recent boost" in fuzzy ranking actually changes order.
 */
const KEY = 'jf-recent-files';
const MAX = 30;

function read(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((p): p is string => typeof p === 'string');
  } catch {
    return [];
  }
}

export function getRecentFiles(): string[] {
  return read();
}

export function pushRecentFile(path: string): void {
  if (!path) return;
  const next = [path, ...read().filter((p) => p !== path)].slice(0, MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // localStorage full / disabled — silently ignore (recent boost is best-effort)
  }
}

export function clearRecentFiles(): void {
  try {
    localStorage.removeItem(KEY);
  } catch { /* ignore */ }
}

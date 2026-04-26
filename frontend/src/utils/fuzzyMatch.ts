/**
 * Lightweight subsequence fuzzy matcher used by the chat input @-mention
 * picker. Designed for an in-memory list of a few thousand entries — runs
 * synchronously per keystroke without breaking the input feel.
 *
 * Scoring (higher = better):
 *   +50  exact match (basename === query, case-insensitive)
 *   +30  basename startsWith(query)
 *   +20  basename contains(query) as substring
 *   +10  full path contains(query) as substring
 *    +c  per consecutive char in subsequence match (basename),
 *        bonus shrinks as we drift away from the start
 *   +recentBoost (if `recent.indexOf(path) >= 0`):
 *        +200 -- 7 * recencyIndex   (recent[0] gets +200, recent[1] +193, ...)
 *
 * No match (subsequence fails) → returns null. Caller filters those out.
 */
export interface FuzzyCandidate {
  path: string;
  name: string;
  is_dir: boolean;
}

export interface FuzzyResult<T extends FuzzyCandidate> {
  item: T;
  score: number;
  /** Index pairs `[start, end)` in `name` that matched the query characters. */
  nameMatches: number[];
}

function subsequenceScore(haystack: string, needle: string): { score: number; matches: number[] } | null {
  if (!needle) return { score: 0, matches: [] };
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase();
  let hi = 0;
  let ni = 0;
  let score = 0;
  let lastMatch = -2;
  const matches: number[] = [];
  while (hi < h.length && ni < n.length) {
    if (h[hi] === n[ni]) {
      matches.push(hi);
      // Consecutive bonus, decays as match position drifts past start
      const consecutive = hi === lastMatch + 1 ? 3 : 1;
      const startBoost = Math.max(0, 5 - hi);
      score += consecutive + startBoost;
      lastMatch = hi;
      ni++;
    }
    hi++;
  }
  if (ni < n.length) return null;
  return { score, matches };
}

export function fuzzyMatch<T extends FuzzyCandidate>(
  items: T[],
  query: string,
  recentPaths: string[] = [],
  limit = 50,
): FuzzyResult<T>[] {
  const q = query.trim();
  if (!q) {
    // No query — return recent files first (if present in items), then dir-leading items.
    // We do NOT return everything because the picker should be useful even on empty.
    const recentSet = new Set(recentPaths);
    const recents = recentPaths
      .map((p) => items.find((it) => it.path === p))
      .filter((it): it is T => !!it)
      .map<FuzzyResult<T>>((item, idx) => ({
        item, score: 1000 - idx, nameMatches: [],
      }));
    if (recents.length >= limit) return recents.slice(0, limit);
    const others = items
      .filter((it) => !recentSet.has(it.path))
      .slice(0, limit - recents.length)
      .map<FuzzyResult<T>>((item) => ({ item, score: 0, nameMatches: [] }));
    return [...recents, ...others].slice(0, limit);
  }

  const recentIndex = new Map<string, number>();
  recentPaths.forEach((p, i) => recentIndex.set(p, i));

  const results: FuzzyResult<T>[] = [];
  const ql = q.toLowerCase();

  for (const item of items) {
    const name = item.name.toLowerCase();
    const path = item.path.toLowerCase();

    let typeBonus = 0;
    if (name === ql) typeBonus += 50;
    else if (name.startsWith(ql)) typeBonus += 30;
    else if (name.includes(ql)) typeBonus += 20;
    else if (path.includes(ql)) typeBonus += 10;

    const nameSub = subsequenceScore(item.name, q);
    if (!nameSub && typeBonus < 10) {
      // Fall back to path subsequence for queries that target dir prefixes
      const pathSub = subsequenceScore(item.path, q);
      if (!pathSub) continue;
      const recentIdx = recentIndex.get(item.path);
      const recentBoost = recentIdx !== undefined ? Math.max(0, 200 - 7 * recentIdx) : 0;
      results.push({ item, score: pathSub.score + recentBoost, nameMatches: [] });
      continue;
    }
    const subScore = nameSub ? nameSub.score : 0;
    const nameMatches = nameSub ? nameSub.matches : [];
    const recentIdx = recentIndex.get(item.path);
    const recentBoost = recentIdx !== undefined ? Math.max(0, 200 - 7 * recentIdx) : 0;
    results.push({ item, score: typeBonus + subScore + recentBoost, nameMatches });
  }

  results.sort((a, b) => b.score - a.score);
  return results.slice(0, limit);
}

/**
 * Build a highlighted HTML span list from a name + match indices.
 * Used by the picker to bold the matched characters.
 */
export function highlightMatches(name: string, matches: number[]): { text: string; highlight: boolean }[] {
  if (!matches.length) return [{ text: name, highlight: false }];
  const out: { text: string; highlight: boolean }[] = [];
  const matchSet = new Set(matches);
  let buf = '';
  let curHighlight = matchSet.has(0);
  for (let i = 0; i < name.length; i++) {
    const isMatch = matchSet.has(i);
    if (isMatch === curHighlight) {
      buf += name[i];
    } else {
      if (buf) out.push({ text: buf, highlight: curHighlight });
      buf = name[i];
      curHighlight = isMatch;
    }
  }
  if (buf) out.push({ text: buf, highlight: curHighlight });
  return out;
}

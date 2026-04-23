/**
 * Lightweight unified-diff with hunk slicing.
 *
 * 不依赖 jsdiff（~50KB gz）。流程：
 *   1. 把 oldText 在 originalText 中定位 → 算出旧片段的起止行
 *   2. 用旧片段 lines / 新片段 lines 做行级 LCS-like 对齐（与 ApprovalCard
 *      内的 computeLineDiff 同一思路），得到改动行序列
 *   3. 把改动行序列回填到原文件视图：旧片段范围内按对齐结果展开为
 *      `+`/`-`/` `（context）行；旧片段之外仍是原文（context）
 *   4. 按上下文行数（默认 3）切 hunk，相邻 hunk 重叠合并
 *
 * 输出：DiffHunk[]，每个 hunk 含 oldStart/oldLines、newStart/newLines、
 * 一组 DiffLine（type / oldNum / newNum / text）。
 */

export type DiffLineType = 'context' | 'add' | 'del';

export interface DiffLine {
  type: DiffLineType;
  oldNum: number | null;
  newNum: number | null;
  text: string;
}

export interface DiffHunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: DiffLine[];
}

export interface DiffResult {
  /** 修改后的整文件行数（用于「展开全文」视图分页判断） */
  totalNewLines: number;
  hunks: DiffHunk[];
  /** 失败原因：'not_found' = old_string 没在原文里找到（agent 写错了 / 文件已被改） */
  error: 'not_found' | null;
  /** 展开全文用：完整的 DiffLine 序列（不切 hunk） */
  fullDiff: DiffLine[];
}

/**
 * 把 newString 替换 originalText 中第一次出现的 oldString，再做行级 diff。
 *
 * 注意：deepagents 的 edit_file 内置语义就是「替换第一次出现」，这里和后端
 * 行为对齐（不做全局替换）。
 */
export function computeUnifiedDiff(
  originalText: string,
  oldString: string,
  newString: string,
  contextLines = 3,
): DiffResult {
  const idx = originalText.indexOf(oldString);
  if (idx < 0) {
    // 旧字符串不在文件里：可能 agent 写错；或文件已被改。
    // 退化策略：全文 vs 全文+附 newString diff（用户至少看到 new_string 是什么）
    return {
      totalNewLines: newString.split('\n').length,
      hunks: [],
      error: 'not_found',
      fullDiff: [],
    };
  }

  // 用替换结果生成新文件
  const newText = originalText.slice(0, idx) + newString + originalText.slice(idx + oldString.length);

  const oldLines = originalText.split('\n');
  const newLines = newText.split('\n');

  const fullDiff = lineDiff(oldLines, newLines);
  const hunks = sliceHunks(fullDiff, contextLines);

  return {
    totalNewLines: newLines.length,
    hunks,
    error: null,
    fullDiff,
  };
}

/**
 * 行级 LCS。返回带类型与新旧行号的 DiffLine 序列。
 *
 * 实现：经典 DP（O(N*M) 内存/时间）。文件中等大小（< 几千行）足够；
 * 极大文件 fallback 到「窗口对齐」可在未来按需加。
 */
export function lineDiff(oldLines: string[], newLines: string[]): DiffLine[] {
  const n = oldLines.length;
  const m = newLines.length;

  // dp[i][j] = LCS length of oldLines[0..i) vs newLines[0..j)
  // 用一维滚动数组省内存
  const prev = new Int32Array(m + 1);
  const curr = new Int32Array(m + 1);
  // 还原路径需要完整 dp：内存换简洁。文件大小通常 < 5000 行，dp 占用可控。
  const dp: Int32Array[] = [new Int32Array(m + 1)];
  for (let i = 1; i <= n; i++) {
    const row = new Int32Array(m + 1);
    for (let j = 1; j <= m; j++) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        row[j] = dp[i - 1][j - 1] + 1;
      } else {
        row[j] = Math.max(dp[i - 1][j], row[j - 1]);
      }
    }
    dp.push(row);
  }
  // suppress unused: prev/curr only kept for future memory-tight version
  void prev; void curr;

  // 回溯
  const out: DiffLine[] = [];
  let i = n;
  let j = m;
  while (i > 0 && j > 0) {
    if (oldLines[i - 1] === newLines[j - 1]) {
      out.push({ type: 'context', oldNum: i, newNum: j, text: oldLines[i - 1] });
      i--; j--;
    } else if (dp[i - 1][j] > dp[i][j - 1]) {
      // 严格大于：保证回溯优先走 add 分支（j--），reverse 后输出
      // 「del 先于 add」的 git 习惯顺序。
      out.push({ type: 'del', oldNum: i, newNum: null, text: oldLines[i - 1] });
      i--;
    } else {
      out.push({ type: 'add', oldNum: null, newNum: j, text: newLines[j - 1] });
      j--;
    }
  }
  while (i > 0) {
    out.push({ type: 'del', oldNum: i, newNum: null, text: oldLines[i - 1] });
    i--;
  }
  while (j > 0) {
    out.push({ type: 'add', oldNum: null, newNum: j, text: newLines[j - 1] });
    j--;
  }
  out.reverse();
  return out;
}

/**
 * 按上下文行数切 hunk，相邻 hunk 重叠时合并。
 *
 * 算法：
 *   - 找出所有「改动行（add/del）」的索引 i
 *   - 对每个改动行，标记 [i - ctx, i + ctx] 范围内的行号为 keep
 *   - keep 范围连成 hunk
 */
export function sliceHunks(full: DiffLine[], contextLines: number): DiffHunk[] {
  if (full.length === 0) return [];

  const keep = new Uint8Array(full.length);
  for (let i = 0; i < full.length; i++) {
    if (full[i].type !== 'context') {
      const lo = Math.max(0, i - contextLines);
      const hi = Math.min(full.length - 1, i + contextLines);
      for (let k = lo; k <= hi; k++) keep[k] = 1;
    }
  }

  const hunks: DiffHunk[] = [];
  let i = 0;
  while (i < full.length) {
    if (!keep[i]) { i++; continue; }
    const start = i;
    while (i < full.length && keep[i]) i++;
    const end = i; // exclusive

    const slice = full.slice(start, end);
    // 计算 hunk 头：第一个有 oldNum / newNum 的行
    const firstOldNum = slice.find(l => l.oldNum != null)?.oldNum ?? 1;
    const firstNewNum = slice.find(l => l.newNum != null)?.newNum ?? 1;
    const oldCount = slice.filter(l => l.type !== 'add').length;
    const newCount = slice.filter(l => l.type !== 'del').length;

    hunks.push({
      oldStart: firstOldNum,
      oldLines: oldCount,
      newStart: firstNewNum,
      newLines: newCount,
      lines: slice,
    });
  }
  return hunks;
}

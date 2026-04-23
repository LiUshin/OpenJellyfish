/**
 * EditDiffViewer — git-style 带上下文的 unified diff 视图。
 *
 * 触发：edit_file 工具在「流式完成」之后切到这个组件（StreamingFilePreview /
 * ApprovalCard 内部判断），把 old_string + new_string 还原到原文件中的位置，
 * 显示完整带上下文 diff。
 *
 * 加载：异步 GET /api/files/read 拿原文件全文 → 在原文中 indexOf old_string →
 * 计算 unified diff（默认 3 行上下文）→ 渲染 hunk。
 *
 * 边界：
 *   - 原文未找到 old_string（agent 写错或文件已被改）：降级到双段对照
 *     （直接显示 old_string 全部 - 与 new_string 全部 +），不报错阻断 UI
 *   - fetch 失败 / 路径不存在（新文件 edit）：同样降级到双段对照
 *   - 大文件 / 大量 hunk：默认显示 hunk 视图，按钮可切换「展开全文」
 */

import { useState, useEffect, useMemo } from 'react';
import hljs from 'highlight.js/lib/core';
import { FileCode, ArrowsOutSimple, ArrowsInSimple, CircleNotch, Warning } from '@phosphor-icons/react';
import * as api from '../../../services/api';
import { computeUnifiedDiff, lineDiff, type DiffLine, type DiffHunk } from '../../../utils/unifiedDiff';
import { escapeHtml } from '../markdown';
import '../markdown';
import styles from '../chat.module.css';

interface Props {
  filePath: string;
  oldString: string;
  newString: string;
  /** 上下文行数，默认 3（git -U3） */
  contextLines?: number;
  /** 顶部状态徽章（外层 ApprovalCard 在 pending 时传 'pending'，普通完成传 'done'） */
  status?: 'pending' | 'done' | 'error';
  /** 错误状态时的简短信息 */
  errorMessage?: string;
}

const EXT_TO_LANG: Record<string, string> = {
  py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript', jsx: 'javascript',
  json: 'json', sh: 'bash', bash: 'bash', zsh: 'bash',
  yml: 'yaml', yaml: 'yaml', md: 'markdown', markdown: 'markdown',
  html: 'html', htm: 'html', xml: 'xml', svg: 'xml',
  css: 'css', scss: 'css', less: 'css',
  java: 'java', go: 'go', rs: 'rust',
  cpp: 'cpp', cc: 'cpp', cxx: 'cpp', c: 'cpp', h: 'cpp', hpp: 'cpp',
  sql: 'sql', toml: 'toml', ini: 'ini', cfg: 'ini', conf: 'ini',
  dockerfile: 'dockerfile',
};

function detectLang(filePath: string): string | undefined {
  const base = filePath.split('/').pop() || filePath;
  if (/^Dockerfile/i.test(base)) return 'dockerfile';
  const ext = base.split('.').pop()?.toLowerCase() ?? '';
  return EXT_TO_LANG[ext];
}

function highlightLine(text: string, lang?: string): string {
  if (!text) return '';
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
    }
  } catch {
    /* fallthrough */
  }
  return escapeHtml(text);
}

interface FetchState {
  loading: boolean;
  error: string | null;
  originalText: string | null;
}

function useOriginalFile(filePath: string): FetchState {
  const [state, setState] = useState<FetchState>({ loading: true, error: null, originalText: null });

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, originalText: null });
    api.readFile(filePath)
      .then(res => {
        if (cancelled) return;
        setState({ loading: false, error: null, originalText: res.content ?? '' });
      })
      .catch(err => {
        if (cancelled) return;
        setState({ loading: false, error: err?.message || '无法读取原文件', originalText: null });
      });
    return () => { cancelled = true; };
  }, [filePath]);

  return state;
}

function HunkHeader({ hunk }: { hunk: DiffHunk }) {
  return (
    <div className={styles.diffHunkHeader}>
      <span className={styles.diffHunkRange}>
        @@ -{hunk.oldStart},{hunk.oldLines} +{hunk.newStart},{hunk.newLines} @@
      </span>
    </div>
  );
}

function DiffLineRow({ line, lang }: { line: DiffLine; lang?: string }) {
  const cls =
    line.type === 'add' ? styles.diffRowAdd
      : line.type === 'del' ? styles.diffRowDel
        : styles.diffRowContext;
  const sign = line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' ';
  return (
    <div className={`${styles.diffRow} ${cls}`}>
      <span className={styles.diffRowOldNum}>{line.oldNum ?? ''}</span>
      <span className={styles.diffRowNewNum}>{line.newNum ?? ''}</span>
      <span className={styles.diffRowSign}>{sign}</span>
      <code
        className={`${styles.diffRowText} hljs${lang ? ' language-' + lang : ''}`}
        dangerouslySetInnerHTML={{ __html: highlightLine(line.text, lang) || '&nbsp;' }}
      />
    </div>
  );
}

interface DiffBodyData {
  hunks: DiffHunk[];
  fullDiff: DiffLine[];
  notFound: boolean;
  /** 没有 fetch 成功（新文件、读取失败）：fallback 显示模式 */
  fallback: boolean;
}

export default function EditDiffViewer({
  filePath,
  oldString,
  newString,
  contextLines = 3,
  status = 'done',
  errorMessage,
}: Props) {
  const { loading, error, originalText } = useOriginalFile(filePath);
  const [expanded, setExpanded] = useState(false);
  const lang = useMemo(() => detectLang(filePath), [filePath]);

  const data: DiffBodyData = useMemo(() => {
    if (originalText == null) {
      // 还在加载或 fetch 失败：先用「双段对照」兜底（old_string 全删 / new_string 全增）
      const oldLines = oldString.split('\n');
      const newLines = newString.split('\n');
      const fullDiff = lineDiff(oldLines, newLines);
      return {
        hunks: [{
          oldStart: 1,
          oldLines: oldLines.length,
          newStart: 1,
          newLines: newLines.length,
          lines: fullDiff,
        }],
        fullDiff,
        notFound: false,
        fallback: true,
      };
    }
    const result = computeUnifiedDiff(originalText, oldString, newString, contextLines);
    if (result.error === 'not_found') {
      const oldLines = oldString.split('\n');
      const newLines = newString.split('\n');
      const fullDiff = lineDiff(oldLines, newLines);
      return {
        hunks: [{
          oldStart: 1,
          oldLines: oldLines.length,
          newStart: 1,
          newLines: newLines.length,
          lines: fullDiff,
        }],
        fullDiff,
        notFound: true,
        fallback: false,
      };
    }
    return { hunks: result.hunks, fullDiff: result.fullDiff, notFound: false, fallback: false };
  }, [originalText, oldString, newString, contextLines]);

  // 状态徽章
  let statusNode: React.ReactNode;
  let statusClass = '';
  if (status === 'error') {
    statusNode = <span>失败</span>;
    statusClass = styles.streamFileStatusError;
  } else if (status === 'pending') {
    statusNode = <span>待审批</span>;
    statusClass = styles.streamFileStatusPending;
  } else {
    const adds = data.fullDiff.filter(l => l.type === 'add').length;
    const dels = data.fullDiff.filter(l => l.type === 'del').length;
    statusNode = (
      <>
        <span>已编辑</span>
        {adds > 0 && <span className={styles.diffStatAdd}>+{adds}</span>}
        {dels > 0 && <span className={styles.diffStatDel}>-{dels}</span>}
      </>
    );
    statusClass = styles.streamFileStatusDone;
  }

  const showFull = expanded && !data.fallback && !data.notFound;
  const renderLines = showFull ? data.fullDiff : null;

  return (
    <div className={styles.streamFileCard}>
      <div className={styles.streamFileHeader}>
        <div className={styles.streamFileHeaderLeft}>
          <FileCode size={16} weight="duotone" />
          <span className={styles.streamFilePath} title={filePath}>{filePath}</span>
          {lang && <span className={styles.streamFileLang}>{lang}</span>}
        </div>
        <div className={styles.streamFileHeaderRight}>
          <div className={`${styles.streamFileStatus} ${statusClass}`}>{statusNode}</div>
          {!data.fallback && !data.notFound && (
            <button
              type="button"
              className={styles.diffExpandBtn}
              onClick={() => setExpanded(v => !v)}
              title={expanded ? '收起为 hunk 视图' : '展开整文件'}
            >
              {expanded ? <ArrowsInSimple size={12} /> : <ArrowsOutSimple size={12} />}
              <span>{expanded ? '收起' : '展开全文'}</span>
            </button>
          )}
        </div>
      </div>

      {loading && (
        <div className={styles.diffLoading}>
          <span className={styles.streamFileSpin}><CircleNotch size={14} weight="bold" /></span>
          <span>读取原文件…</span>
        </div>
      )}

      {!loading && (data.fallback || data.notFound) && (
        <div className={styles.diffWarn}>
          <Warning size={14} weight="duotone" />
          {data.notFound
            ? '原文件中找不到 old_string，下方仅显示模型给出的替换片段对照'
            : `${error ?? '原文件不可读'}，下方仅显示模型给出的替换片段对照`}
        </div>
      )}

      <div className={styles.diffBody}>
        {showFull && renderLines && renderLines.map((line, i) => (
          <DiffLineRow key={`f-${i}`} line={line} lang={lang} />
        ))}
        {!showFull && data.hunks.map((hunk, hi) => (
          <div key={`h-${hi}`} className={styles.diffHunk}>
            {!data.fallback && !data.notFound && <HunkHeader hunk={hunk} />}
            {hunk.lines.map((line, li) => (
              <DiffLineRow key={`h-${hi}-l-${li}`} line={line} lang={lang} />
            ))}
          </div>
        ))}
        {!showFull && data.hunks.length === 0 && !loading && (
          <div className={styles.diffEmpty}>无变化</div>
        )}
      </div>

      {status === 'error' && errorMessage && (
        <div className={styles.streamFileError}>{errorMessage.slice(0, 500)}</div>
      )}
    </div>
  );
}

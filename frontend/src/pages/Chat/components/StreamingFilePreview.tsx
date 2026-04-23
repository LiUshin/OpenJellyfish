/**
 * StreamingFilePreview — 流式文件写入/编辑可视化。
 *
 * 触发：当 ToolBlock.name 是 `write_file` 或 `edit_file` 时，
 * 替代默认的 ToolIndicator/ServiceToolBadge 使用本组件，呈现：
 *
 *   ┌─────────────────────────────────────────────────────┐
 *   │ 📝 写入文件 path/to/file.py     [流式中…/已完成]    │
 *   ├─────────────────────────────────────────────────────┤
 *   │ 1│ import os                                        │
 *   │ 2│ def main():                                      │
 *   │ 3│     print("hi")▍                                 │  ← 打字机光标
 *   └─────────────────────────────────────────────────────┘
 *
 * 数据来源：
 * - LLM 流式生成 tool_call args（{"file_path":"...","content":"..."}），
 *   chat.py SSE 把每段 args_delta 累加到 ToolBlock.args
 * - 这里用 partialJson.extractStreamingField 从 incomplete JSON 中实时
 *   提取 content / new_string / file_path 字段
 *
 * 高亮：
 * - 复用 markdown.ts 已注册到 highlight.js 全局单例的语言（py/ts/json/...）
 * - 文件后缀 → lang 映射，未识别的走 highlightAuto
 *
 * 不在本组件做的事：
 * - 审批按钮（由 ApprovalCard 套在外层渲染）
 * - 工具结果（block.result）展示 —— 写入成功无信息价值，失败时简短显示
 */

import { useMemo } from 'react';
import hljs from 'highlight.js/lib/core';
import { FileArrowUp, FileCode, CircleNotch, CheckCircle, XCircle } from '@phosphor-icons/react';
import type { ToolBlock } from '../types';
import { extractStreamingField } from '../../../utils/partialJson';
import { escapeHtml } from '../markdown';
// 副作用 import：确保 markdown.ts 被加载，从而完成 hljs 语言注册
import '../markdown';
import EditDiffViewer from './EditDiffViewer';
import styles from '../chat.module.css';

interface Props {
  block: ToolBlock;
  /** 是否为正在流式输出的最后一个 block；只有 true 时才显示打字机光标。 */
  isStreaming?: boolean;
}

/** FilePreviewBody 的纯展示状态。
 *  - streaming: 流式中（带光标 + 旋转图标）
 *  - pending:  生成完成、等待用户审批（HITL）
 *  - done:     已写入/已编辑成功
 *  - error:    工具返回失败
 */
export type FilePreviewStatus = 'streaming' | 'pending' | 'done' | 'error';

export interface FilePreviewBodyProps {
  filePath: string | null;
  /** write_file: 全文；edit_file: new_string */
  text: string;
  /** 'write' | 'edit' —— 用于头部图标和"已写入/已编辑"文案 */
  kind: 'write' | 'edit';
  status: FilePreviewStatus;
  /** 错误时的简短提示（status='error'）；其它状态忽略 */
  errorMessage?: string;
  /** 显式指定语言（不指定则用 detectLang(filePath)） */
  langOverride?: string;
  /** 是否显示打字机光标（一般等于 status === 'streaming'，这里独立控制以备特殊场景） */
  showCursor?: boolean;
}

const EXT_TO_LANG: Record<string, string> = {
  py: 'python',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  jsx: 'javascript',
  json: 'json',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  yml: 'yaml',
  yaml: 'yaml',
  md: 'markdown',
  markdown: 'markdown',
  html: 'html',
  htm: 'html',
  xml: 'xml',
  svg: 'xml',
  css: 'css',
  scss: 'css',
  less: 'css',
  java: 'java',
  go: 'go',
  rs: 'rust',
  cpp: 'cpp',
  cc: 'cpp',
  cxx: 'cpp',
  c: 'cpp',
  h: 'cpp',
  hpp: 'cpp',
  sql: 'sql',
  toml: 'toml',
  ini: 'ini',
  cfg: 'ini',
  conf: 'ini',
  dockerfile: 'dockerfile',
};

function detectLang(filePath: string | null): string | undefined {
  if (!filePath) return undefined;
  const base = filePath.split('/').pop() || filePath;
  if (/^Dockerfile/i.test(base)) return 'dockerfile';
  const ext = base.split('.').pop()?.toLowerCase() ?? '';
  return EXT_TO_LANG[ext];
}

function highlight(text: string, lang?: string): string {
  if (!text) return '';
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
    }
    return hljs.highlightAuto(text).value;
  } catch {
    return escapeHtml(text);
  }
}

/**
 * 在已渲染的高亮 HTML 末尾安全地插入打字机光标。
 *
 * hljs 输出的 HTML 是良构（标签都闭合的）；我们在最末尾追加 <span class="...cursor">▍</span>。
 * 不要直接拼字符串到 inner text 里，否则会被 hljs 的 token 化破坏样式。
 */
function appendCursor(html: string): string {
  return html + '<span class="' + styles.streamCursor + '">▍</span>';
}

interface ParsedArgs {
  filePath: string | null;
  // write_file: 直接展示 content
  content: string | null;
  // edit_file: 展示 new_string；流式中没有 diff 视图，等 done 后由 ApprovalCard 展示 diff
  oldString: string | null;
  newString: string | null;
  fallbackText: string;
}

function parseArgs(raw: string): ParsedArgs {
  const filePath =
    extractStreamingField(raw, 'file_path') ??
    extractStreamingField(raw, 'path');
  const content = extractStreamingField(raw, 'content');
  const oldString = extractStreamingField(raw, 'old_string');
  const newString = extractStreamingField(raw, 'new_string');

  // 如果还没解析出任何字段（args 还在 `{` 阶段），返回原文给一个简单展示
  const fallback = !filePath && !content && !oldString && !newString;
  return {
    filePath,
    content,
    oldString,
    newString,
    fallbackText: fallback ? raw : '',
  };
}

/**
 * FilePreviewBody —— 纯展示组件。
 * 接收语义化 props（已解析好的 filePath/text/状态），不关心 ToolBlock / partial JSON。
 * ApprovalCard 在 HITL 审批阶段直接用它（status='pending'）。
 */
export function FilePreviewBody({
  filePath,
  text,
  kind,
  status,
  errorMessage,
  langOverride,
  showCursor,
}: FilePreviewBodyProps) {
  const lang = langOverride ?? detectLang(filePath);
  const isWrite = kind === 'write';
  const lineCount = text ? text.split('\n').length : 0;
  const cursorOn = showCursor ?? status === 'streaming';

  let highlighted = highlight(text, lang);
  if (cursorOn) highlighted = appendCursor(highlighted);

  let statusNode: React.ReactNode;
  let statusClass = '';
  switch (status) {
    case 'error':
      statusNode = (<><XCircle size={14} weight="fill" /><span>失败</span></>);
      statusClass = styles.streamFileStatusError;
      break;
    case 'done':
      statusNode = (
        <>
          <CheckCircle size={14} weight="fill" />
          <span>{isWrite ? '已写入' : '已编辑'}</span>
          {lineCount > 0 && <span className={styles.streamFileStatusMeta}>{lineCount} 行</span>}
        </>
      );
      statusClass = styles.streamFileStatusDone;
      break;
    case 'pending':
      statusNode = (
        <>
          <span>待审批</span>
          {lineCount > 0 && <span className={styles.streamFileStatusMeta}>{lineCount} 行</span>}
        </>
      );
      statusClass = styles.streamFileStatusPending;
      break;
    case 'streaming':
    default:
      statusNode = (
        <>
          <span className={styles.streamFileSpin}><CircleNotch size={14} weight="bold" /></span>
          <span>{isWrite ? '正在写入…' : '正在编辑…'}</span>
          {lineCount > 0 && <span className={styles.streamFileStatusMeta}>{lineCount} 行</span>}
        </>
      );
      statusClass = styles.streamFileStatusStreaming;
  }

  const headerPath = filePath || (isWrite ? '准备写入文件…' : '准备编辑文件…');

  return (
    <div className={styles.streamFileCard}>
      <div className={styles.streamFileHeader}>
        <div className={styles.streamFileHeaderLeft}>
          {isWrite ? <FileArrowUp size={16} weight="duotone" /> : <FileCode size={16} weight="duotone" />}
          <span className={styles.streamFilePath} title={filePath ?? ''}>{headerPath}</span>
          {lang && <span className={styles.streamFileLang}>{lang}</span>}
        </div>
        <div className={`${styles.streamFileStatus} ${statusClass}`}>{statusNode}</div>
      </div>

      {text ? (
        <pre className={styles.streamFileBody}>
          <code
            className={`hljs${lang ? ' language-' + lang : ''}`}
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        </pre>
      ) : (
        <div className={styles.streamFileBodyPlaceholder}>
          <span className={styles.streamFileSpin}><CircleNotch size={14} weight="bold" /></span>
          <span>等待内容…</span>
        </div>
      )}

      {status === 'error' && errorMessage && (
        <div className={styles.streamFileError}>{errorMessage.slice(0, 500)}</div>
      )}
    </div>
  );
}

export default function StreamingFilePreview({ block, isStreaming = false }: Props) {
  const isWrite = block.name === 'write_file';
  const parsed = useMemo(() => parseArgs(block.args || ''), [block.args]);

  const body = isWrite ? parsed.content : parsed.newString;
  const isError = block.done && /^(error|failed|failure|✗|❌|失败|出错)/i.test(block.result.trim());

  let status: FilePreviewStatus;
  if (isError) status = 'error';
  else if (block.done) status = 'done';
  else status = isStreaming ? 'streaming' : 'pending';

  // edit_file 在「不再流式」时切换到 git-style EditDiffViewer：
  // - 流式中：显示打字机 new_string（FilePreviewBody，下方逻辑）
  // - 流完（done / pending / error）：拉原文件做带上下文 diff
  // 触发条件：args 已解析出 file_path / old_string / new_string 三个字段
  if (
    !isWrite
    && status !== 'streaming'
    && parsed.filePath
    && parsed.oldString != null
    && parsed.newString != null
  ) {
    return (
      <EditDiffViewer
        filePath={parsed.filePath}
        oldString={parsed.oldString}
        newString={parsed.newString}
        status={status}
        errorMessage={isError ? block.result : undefined}
      />
    );
  }

  // 极少见：args 还在 `{` 阶段，body 和 filePath 都没出来；用 fallbackText 兜底
  if (!body && parsed.fallbackText) {
    return (
      <div className={styles.streamFileCard}>
        <div className={styles.streamFileHeader}>
          <div className={styles.streamFileHeaderLeft}>
            {isWrite ? <FileArrowUp size={16} weight="duotone" /> : <FileCode size={16} weight="duotone" />}
            <span className={styles.streamFilePath}>{isWrite ? '准备写入文件…' : '准备编辑文件…'}</span>
          </div>
          <div className={`${styles.streamFileStatus} ${styles.streamFileStatusStreaming}`}>
            <span className={styles.streamFileSpin}><CircleNotch size={14} weight="bold" /></span>
            <span>等待…</span>
          </div>
        </div>
        <pre className={styles.streamFileBodyEmpty}>{parsed.fallbackText}</pre>
      </div>
    );
  }

  return (
    <FilePreviewBody
      filePath={parsed.filePath}
      text={body ?? ''}
      kind={isWrite ? 'write' : 'edit'}
      status={status}
      errorMessage={block.result}
      showCursor={status === 'streaming'}
    />
  );
}

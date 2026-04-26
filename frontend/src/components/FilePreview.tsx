import { type KeyboardEvent, useMemo, useState, useEffect, useRef, useCallback } from 'react';
import { Button, Tooltip, Segmented, Table, Empty, App } from 'antd';
import {
  SaveOutlined,
  CloseOutlined,
  DownloadOutlined,
  FileUnknownOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import hljs from 'highlight.js/lib/core';
import { useFileWorkspace } from '../stores/fileWorkspaceContext';
import HeaderControls from './HeaderControls';
import * as api from '../services/api';
import {
  getFileKind,
  isMediaKind,
  isToggleKind,
  isEditableKind,
  type FileKind,
} from '../utils/fileKind';
import { parseCsv } from '../utils/csvParse';
import { renderMarkdown, slugifyHeading } from '../pages/Chat/markdown';

const C = {
  bg: 'var(--jf-bg-panel)',
  bgDark: 'var(--jf-bg-deep)',
  text: 'var(--jf-text)',
  textSec: 'var(--jf-text-muted)',
  textDim: 'var(--jf-text-dim)',
  border: 'var(--jf-border)',
  accent: 'var(--jf-legacy)',
};

type ViewMode = 'preview' | 'source';

// ────────────────────────────── 子视图 ──────────────────────────────

function MediaView({ path, kind }: { path: string; kind: FileKind }) {
  const { pendingAnchor, consumePendingAnchor } = useFileWorkspace();
  // PDF #page=N / #zoom= deep-link: appended to iframe src ONLY for kind=pdf.
  // Browser-native PDF viewers (Chrome/Edge/Firefox) honour fragments per
  // Adobe Open Parameters spec — no JS plumbing needed. Other media kinds
  // (image/audio/video) ignore anchor (no semantic anchor to honour).
  const baseUrl = api.mediaUrl(path);
  const url = kind === 'pdf' && pendingAnchor
    ? `${baseUrl}#${encodeURI(pendingAnchor)}`
    : baseUrl;
  const name = path.split('/').pop() || path;
  // One-shot consume: anchor is meant to drive the initial scroll; clearing
  // here means re-opening the same file later without a new reveal won't
  // surprise the user with a stale page jump.
  useEffect(() => {
    if (pendingAnchor && kind === 'pdf') {
      consumePendingAnchor();
    }
  }, [pendingAnchor, kind, consumePendingAnchor]);

  if (kind === 'image') {
    return (
      <div style={mediaWrapStyle}>
        <img
          src={url}
          alt={name}
          style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', borderRadius: 6 }}
        />
      </div>
    );
  }
  if (kind === 'audio') {
    return (
      <div style={mediaWrapStyle}>
        <audio controls preload="metadata" src={url} style={{ width: '90%', maxWidth: 520 }}>
          浏览器不支持音频播放
        </audio>
      </div>
    );
  }
  if (kind === 'video') {
    return (
      <div style={mediaWrapStyle}>
        <video
          controls
          preload="metadata"
          src={url}
          style={{ maxWidth: '100%', maxHeight: '100%', borderRadius: 6 }}
        >
          浏览器不支持视频播放
        </video>
      </div>
    );
  }
  // pdf
  return (
    <iframe
      title={name}
      src={url}
      style={{ flex: 1, width: '100%', border: 'none', background: '#fff' }}
    />
  );
}

const mediaWrapStyle: React.CSSProperties = {
  flex: 1,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: C.bgDark,
  padding: 16,
  overflow: 'auto',
};

interface TocEntry {
  id: string;
  level: number;
  text: string;
}

/** Parse h1-h3 ids out of the rendered HTML so we can power a TOC overlay
 *  without re-parsing markdown. We use DOMParser on the cached HTML output
 *  (one pass per content change) — cheaper than running marked twice and
 *  doesn't drift from the actual ids that markdown.ts injected. h4-h6 are
 *  intentionally excluded: a 6-level TOC overwhelms the side rail and is
 *  rarely how authors structure docs anyway. */
function extractToc(html: string): TocEntry[] {
  if (typeof DOMParser === 'undefined') return [];
  try {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const entries: TocEntry[] = [];
    doc.querySelectorAll('h1, h2, h3').forEach((el) => {
      const id = el.getAttribute('id');
      if (!id) return;
      const text = (el.textContent || '').replace(/#$/, '').trim();
      if (!text) return;
      entries.push({ id, level: parseInt(el.tagName.slice(1), 10), text });
    });
    return entries;
  } catch {
    return [];
  }
}

function MarkdownPreview({ content }: { content: string }) {
  const { pendingAnchor, consumePendingAnchor } = useFileWorkspace();
  const html = useMemo(() => renderMarkdown(content), [content]);
  const toc = useMemo(() => extractToc(html), [html]);
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  // TOC starts collapsed on small/medium previews to avoid covering content;
  // user can pin it open via the toggle button.
  const [tocOpen, setTocOpen] = useState(true);

  // Deep-link via <<FILE:/x.md#标题>>: when MarkdownPreview mounts (or html
  // changes) and a pendingAnchor is present, try multiple resolution strategies
  // to find the matching heading and scroll to it. Agents may write either:
  //   - the raw heading text (`#我的章节`)
  //   - the already-slugified id (`#wo-de-zhang-jie`)
  //   - a substring that uniquely identifies a heading (best-effort)
  // Silently no-op if nothing matches — never surface an error toast for this.
  useEffect(() => {
    if (!pendingAnchor || !containerRef.current) return;
    const root = containerRef.current;
    // Wait one frame for innerHTML / TOC IntersectionObserver to settle, then
    // resolve the anchor and scroll. requestAnimationFrame is enough — no need
    // for setTimeout(0) which is racier under React 18 strict-mode double-mount.
    const raf = requestAnimationFrame(() => {
      const escapeId = (id: string) => {
        try { return CSS.escape(id); } catch { return id; }
      };
      // 1. Direct id match (agent wrote the slug or CJK plain text already passes through).
      let target = root.querySelector(`#${escapeId(pendingAnchor)}`) as HTMLElement | null;
      // 2. Slugify and try again (agent wrote the raw heading text).
      if (!target) {
        const slug = slugifyHeading(pendingAnchor);
        if (slug) target = root.querySelector(`#${escapeId(slug)}`) as HTMLElement | null;
      }
      // 3. Fallback: case-insensitive substring match on heading text.
      if (!target) {
        const needle = pendingAnchor.trim().toLowerCase();
        const candidates = root.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6');
        for (const h of candidates) {
          const txt = (h.textContent || '').replace(/#$/, '').trim().toLowerCase();
          if (txt === needle || txt.includes(needle)) {
            target = h;
            break;
          }
        }
      }
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        setActiveId(target.id || null);
      }
      // Always consume — if anchor doesn't resolve, silently open the file
      // at its top (per design choice in user's task spec).
      consumePendingAnchor();
    });
    return () => cancelAnimationFrame(raf);
  }, [pendingAnchor, html, consumePendingAnchor]);

  // Track which heading is currently in view (Reading-Progress style).
  // We use IntersectionObserver scoped to the scroll container to keep the
  // TOC's "current" highlight in sync with the user's reading position.
  useEffect(() => {
    if (toc.length === 0 || !containerRef.current) return;
    const root = containerRef.current;
    const headings = toc
      .map((t) => root.querySelector(`#${CSS.escape(t.id)}`))
      .filter((el): el is HTMLElement => el !== null);
    if (headings.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        // Pick the topmost heading currently intersecting; fallback to the
        // last-seen id if nothing intersects (long sections between titles).
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) {
          setActiveId((visible[0].target as HTMLElement).id);
        }
      },
      { root, rootMargin: '-10% 0px -75% 0px', threshold: [0, 1] },
    );
    headings.forEach((h) => observer.observe(h));
    return () => observer.disconnect();
  }, [toc, html]);

  const scrollToHeading = useCallback((id: string) => {
    const el = containerRef.current?.querySelector(`#${CSS.escape(id)}`);
    if (el) {
      (el as HTMLElement).scrollIntoView({ behavior: 'smooth', block: 'start' });
      setActiveId(id);
    }
  }, []);

  const showToc = toc.length >= 2;

  return (
    <div style={{ flex: 1, position: 'relative', display: 'flex', minHeight: 0 }}>
      <div
        ref={containerRef}
        className="jf-markdown jf-file-md-preview"
        style={{
          flex: 1,
          overflow: 'auto',
          padding: '20px 24px',
          background: C.bg,
          color: C.text,
          fontSize: 14,
          lineHeight: 1.7,
          minWidth: 0,
        }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {showToc && tocOpen && (
        <aside
          style={{
            width: 220,
            flexShrink: 0,
            background: C.bgDark,
            borderLeft: `1px solid ${C.border}`,
            overflowY: 'auto',
            padding: '14px 6px 14px 12px',
            fontSize: 12,
            color: C.textSec,
            position: 'relative',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              paddingBottom: 8,
              marginBottom: 6,
              borderBottom: `1px solid ${C.border}`,
              fontWeight: 500,
              color: C.text,
            }}
          >
            <span>目录</span>
            <Button
              type="text"
              size="small"
              icon={<CloseOutlined style={{ fontSize: 11 }} />}
              onClick={() => setTocOpen(false)}
              style={{ color: C.textDim, width: 22, height: 22, minWidth: 22 }}
              title="收起目录"
            />
          </div>
          {toc.map((entry) => (
            <button
              key={entry.id}
              onClick={() => scrollToHeading(entry.id)}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: '4px 8px',
                paddingLeft: 8 + (entry.level - 1) * 12,
                marginBottom: 1,
                background: activeId === entry.id ? 'rgba(var(--jf-primary-rgb), 0.18)' : 'transparent',
                color: activeId === entry.id ? C.text : C.textSec,
                border: 'none',
                borderLeft: activeId === entry.id ? '2px solid var(--jf-primary)' : '2px solid transparent',
                cursor: 'pointer',
                fontSize: entry.level === 1 ? 12.5 : entry.level === 2 ? 12 : 11.5,
                fontWeight: entry.level === 1 ? 500 : 400,
                borderRadius: 'var(--jf-radius-sm)',
                transition: 'background 0.15s',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
              onMouseEnter={(e) => {
                if (activeId !== entry.id) e.currentTarget.style.background = 'rgba(108, 92, 231, 0.06)';
              }}
              onMouseLeave={(e) => {
                if (activeId !== entry.id) e.currentTarget.style.background = 'transparent';
              }}
              title={entry.text}
            >
              {entry.text}
            </button>
          ))}
        </aside>
      )}
      {showToc && !tocOpen && (
        <Button
          type="text"
          size="small"
          icon={<UnorderedListOutlined />}
          onClick={() => setTocOpen(true)}
          style={{
            position: 'absolute',
            top: 12,
            right: 12,
            color: C.textSec,
            background: C.bgDark,
            border: `1px solid ${C.border}`,
            borderRadius: 'var(--jf-radius-sm)',
            zIndex: 5,
          }}
          title={`目录（${toc.length} 项）`}
        />
      )}
    </div>
  );
}

function HtmlPreview({ path }: { path: string }) {
  // 直接以 iframe 加载后端 media 接口，sandbox=allow-scripts（无 same-origin）
  const url = api.mediaUrl(path);
  return (
    <iframe
      title={path}
      src={url}
      sandbox="allow-scripts"
      style={{ flex: 1, width: '100%', border: 'none', background: '#fff' }}
    />
  );
}

function CsvPreview({ content }: { content: string }) {
  const parsed = useMemo(() => parseCsv(content), [content]);
  const { rows, totalRows, truncated, delimiter } = parsed;

  if (rows.length === 0) {
    return (
      <div style={emptyWrapStyle}>
        <Empty description="空 CSV" />
      </div>
    );
  }

  const header = rows[0];
  const dataRows = rows.slice(1);
  const columns = header.map((h, idx) => ({
    title: h || `col${idx + 1}`,
    dataIndex: String(idx),
    key: String(idx),
    ellipsis: true,
    width: 160,
  }));
  const data = dataRows.map((r, ridx) => {
    const obj: Record<string, string> = { key: String(ridx) };
    r.forEach((cell, cidx) => {
      obj[String(cidx)] = cell;
    });
    return obj;
  });

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, background: C.bg }}>
      <div
        style={{
          padding: '6px 14px',
          fontSize: 11,
          color: C.textDim,
          borderBottom: `1px solid ${C.border}`,
          display: 'flex',
          gap: 12,
          flexShrink: 0,
        }}
      >
        <span>分隔符：{delimiter === '\t' ? 'TAB' : `"${delimiter}"`}</span>
        <span>行数：{totalRows}{truncated ? `（仅显示前 ${rows.length} 行）` : ''}</span>
        <span>列数：{header.length}</span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        <Table
          size="small"
          columns={columns}
          dataSource={data}
          pagination={false}
          scroll={{ x: 'max-content' }}
          bordered
        />
      </div>
    </div>
  );
}

function JsonPreview({ content }: { content: string }) {
  const { html, error } = useMemo(() => {
    try {
      const parsed = JSON.parse(content);
      const pretty = JSON.stringify(parsed, null, 2);
      const highlighted = hljs.getLanguage('json')
        ? hljs.highlight(pretty, { language: 'json' }).value
        : escapeHtml(pretty);
      return { html: highlighted, error: null as string | null };
    } catch (e) {
      return { html: '', error: e instanceof Error ? e.message : String(e) };
    }
  }, [content]);

  if (error) {
    return (
      <div style={{ flex: 1, overflow: 'auto', padding: 16, background: C.bgDark }}>
        <div
          style={{
            color: '#e74c3c',
            fontSize: 12,
            padding: '8px 12px',
            background: 'rgba(231,76,60,0.08)',
            borderRadius: 4,
            marginBottom: 12,
          }}
        >
          JSON 解析失败：{error}（已切换为源码模式）
        </div>
        <pre
          style={{
            margin: 0,
            color: C.text,
            fontSize: 12,
            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {content}
        </pre>
      </div>
    );
  }

  return (
    <div
      style={{
        flex: 1,
        overflow: 'auto',
        padding: '14px 16px',
        background: C.bgDark,
      }}
    >
      <pre
        className="hljs language-json"
        style={{
          margin: 0,
          background: 'transparent',
          color: C.text,
          fontSize: 12.5,
          lineHeight: 1.6,
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
        }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}

function escapeHtml(str: string): string {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function BinaryView({ path }: { path: string }) {
  const { message } = App.useApp();
  const name = path.split('/').pop() || path;
  const handleDownload = async () => {
    try {
      const res = await api.downloadFile(path);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败');
    }
  };
  return (
    <div style={emptyWrapStyle}>
      <Empty
        image={<FileUnknownOutlined style={{ fontSize: 64, color: C.textDim }} />}
        description={
          <div style={{ color: C.textSec, fontSize: 13 }}>
            未知二进制文件，无法预览
            <div style={{ fontSize: 11, color: C.textDim, marginTop: 4 }}>{name}</div>
          </div>
        }
      >
        <Button icon={<DownloadOutlined />} onClick={handleDownload}>
          下载文件
        </Button>
      </Empty>
    </div>
  );
}

const emptyWrapStyle: React.CSSProperties = {
  flex: 1,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: C.bgDark,
  padding: 24,
};

function TextEditor({
  content,
  onChange,
  onCmdS,
}: {
  content: string;
  onChange: (s: string) => void;
  onCmdS: () => void;
}) {
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      onCmdS();
    }
  };
  return (
    <textarea
      style={{
        flex: 1,
        background: C.bgDark,
        color: C.text,
        border: 'none',
        outline: 'none',
        padding: '14px 16px',
        fontSize: 13,
        fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
        lineHeight: 1.6,
        resize: 'none',
        width: '100%',
      }}
      value={content}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={handleKeyDown}
      spellCheck={false}
    />
  );
}

// ────────────────────────────── 主组件 ──────────────────────────────

export default function FilePreview() {
  const {
    editingFile,
    editContent,
    editDirty,
    saving,
    saveFile,
    closeFile,
    setEditContent,
  } = useFileWorkspace();

  const [viewMode, setViewMode] = useState<ViewMode>('preview');
  const fileName = editingFile ? editingFile.split('/').pop() || editingFile : '';
  const kind: FileKind = editingFile ? getFileKind(fileName) : 'text';

  // 切换文件时重置 viewMode 为 preview
  useEffect(() => {
    if (editingFile && isToggleKind(kind)) {
      setViewMode('preview');
    }
  }, [editingFile, kind]);

  if (!editingFile) return null;

  const showSaveBtn = isEditableKind(kind);
  const showToggle = isToggleKind(kind);
  const handleDownload = async () => {
    try {
      const res = await api.downloadFile(editingFile);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = fileName;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // 静默：用户可在 BinaryView 内重试
    }
  };

  let body: React.ReactNode = null;
  if (isMediaKind(kind)) {
    body = <MediaView path={editingFile} kind={kind} />;
  } else if (kind === 'binary') {
    body = <BinaryView path={editingFile} />;
  } else if (showToggle && viewMode === 'preview') {
    if (kind === 'markdown') body = <MarkdownPreview content={editContent} />;
    else if (kind === 'html') body = <HtmlPreview path={editingFile} />;
    else if (kind === 'csv') body = <CsvPreview content={editContent} />;
    else if (kind === 'json') body = <JsonPreview content={editContent} />;
  } else {
    // text 或 toggle 类的源码视图
    body = (
      <TextEditor content={editContent} onChange={setEditContent} onCmdS={saveFile} />
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: C.bg,
        minWidth: 0,
      }}
    >
      <div
        style={{
          padding: '0 14px',
          height: 47,
          boxSizing: 'border-box',
          borderBottom: `1px solid ${C.border}`,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexShrink: 0,
        }}
      >
        {showSaveBtn && (
          <Tooltip title={editDirty ? '保存 (Ctrl+S)' : '已保存'}>
            <Button
              type="text"
              size="small"
              icon={<SaveOutlined />}
              disabled={!editDirty}
              loading={saving}
              style={{ color: editDirty ? C.accent : C.textDim, flexShrink: 0 }}
              onClick={saveFile}
            />
          </Tooltip>
        )}
        <span
          style={{
            color: C.text,
            fontSize: 13,
            fontWeight: 500,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
          }}
        >
          {fileName}
          {editDirty && <span style={{ color: C.accent }}> ●</span>}
        </span>
        {showToggle && (
          <Segmented
            size="small"
            value={viewMode}
            onChange={(v) => setViewMode(v as ViewMode)}
            options={[
              { label: '预览', value: 'preview' },
              { label: '源码', value: 'source' },
            ]}
            style={{ flexShrink: 0 }}
          />
        )}
        <Tooltip title="下载">
          <Button
            type="text"
            size="small"
            icon={<DownloadOutlined />}
            style={{ color: C.textSec }}
            onClick={handleDownload}
          />
        </Tooltip>
        <Tooltip title="关闭文件">
          <Button
            type="text"
            size="small"
            icon={<CloseOutlined />}
            style={{ color: C.textSec }}
            onClick={() => closeFile()}
          />
        </Tooltip>
        <div style={{ width: 1, height: 16, background: C.border, flexShrink: 0 }} />
        <HeaderControls />
      </div>
      {body}
    </div>
  );
}

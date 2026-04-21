import { type KeyboardEvent, useMemo, useState, useEffect } from 'react';
import { Button, Tooltip, Segmented, Table, Empty, App } from 'antd';
import {
  SaveOutlined,
  CloseOutlined,
  DownloadOutlined,
  FileUnknownOutlined,
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
import { renderMarkdown } from '../pages/Chat/markdown';

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
  const url = api.mediaUrl(path);
  const name = path.split('/').pop() || path;

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

function MarkdownPreview({ content }: { content: string }) {
  const html = useMemo(() => renderMarkdown(content), [content]);
  return (
    <div
      className="jf-markdown jf-file-md-preview"
      style={{
        flex: 1,
        overflow: 'auto',
        padding: '20px 24px',
        background: C.bg,
        color: C.text,
        fontSize: 14,
        lineHeight: 1.7,
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
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

import { useState, useEffect, useCallback, useRef, type DragEvent, type MouseEvent as ReactMouseEvent } from 'react';
import { Button, Input, Tooltip, Spin, App } from 'antd';
import {
  FolderOutlined,
  FolderOpenOutlined,
  ArrowLeftOutlined,
  ReloadOutlined,
  PlusOutlined,
  UploadOutlined,
  DeleteOutlined,
  EditOutlined,
  DownloadOutlined,
  CloseOutlined,
  FolderAddOutlined,
  HomeOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import * as api from '../services/api';
import type { FileItem } from '../types';
import { useFileWorkspace } from '../stores/fileWorkspaceContext';

const ICON_MAP: Record<string, string> = {
  md: '📄', txt: '📄',
  json: '📋', csv: '📊', py: '🐍',
  js: '📜', ts: '📜', jsx: '📜', tsx: '📜',
  html: '🌐', htm: '🌐',
  css: '🎨', scss: '🎨', less: '🎨',
  pdf: '📕', xlsx: '📗', xls: '📗',
  png: '🖼', jpg: '🖼', jpeg: '🖼', gif: '🖼', svg: '🖼', webp: '🖼',
  zip: '📦', rar: '📦', tar: '📦', gz: '📦',
};

function getFileIcon(name: string, isDir: boolean): string {
  if (isDir) return '📁';
  const ext = name.split('.').pop()?.toLowerCase() || '';
  return ICON_MAP[ext] || '📄';
}

function formatSize(bytes?: number): string {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function joinPath(...parts: string[]): string {
  return parts.join('/').replace(/\/+/g, '/').replace(/\/$/, '') || '/';
}

const C = {
  bg: 'var(--jf-bg-panel)',
  bgDark: 'var(--jf-bg-deep)',
  border: 'var(--jf-border)',
  text: 'var(--jf-text)',
  textSec: 'var(--jf-text-muted)',
  textDim: 'var(--jf-text-dim)',
  accent: 'var(--jf-legacy)',
  danger: '#e74c3c',
};

function readEntryAsFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

function readDirectoryEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

async function collectFilesFromEntries(entries: FileSystemEntry[]): Promise<File[]> {
  const files: File[] = [];
  const queue = [...entries];
  while (queue.length > 0) {
    const entry = queue.shift()!;
    if (entry.isFile) {
      const file = await readEntryAsFile(entry as FileSystemFileEntry);
      const relativePath = entry.fullPath.replace(/^\//, '');
      Object.defineProperty(file, 'webkitRelativePath', { value: relativePath });
      files.push(file);
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      let batch: FileSystemEntry[];
      do {
        batch = await readDirectoryEntries(reader);
        queue.push(...batch);
      } while (batch.length > 0);
    }
  }
  return files;
}

const MIN_W = 220;
const MAX_W = 420;
const DEFAULT_W = 280;

export default function FilePanel() {
  const { message, modal } = App.useApp();
  const { fileBrowserOpen, setFileBrowserOpen, editingFile, openFile } = useFileWorkspace();

  const [currentPath, setCurrentPath] = useState('/');
  const [files, setFiles] = useState<FileItem[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [hoveredItem, setHoveredItem] = useState<string | null>(null);
  const [resizeHover, setResizeHover] = useState(false);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_W);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const draggingRef = useRef(false);

  const onResizeStart = useCallback((e: ReactMouseEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    const startX = e.clientX;
    const startW = panelWidth;
    const onMove = (ev: globalThis.MouseEvent) => {
      setPanelWidth(Math.max(MIN_W, Math.min(MAX_W, startW + (startX - ev.clientX))));
    };
    const onUp = () => {
      draggingRef.current = false;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [panelWidth]);

  const loadFiles = useCallback(async (path: string) => {
    setLoadingFiles(true);
    try {
      const items = await api.listFiles(path);
      const sorted = [...items].sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      setFiles(sorted);
    } catch {
      message.error('加载文件列表失败');
      setFiles([]);
    } finally {
      setLoadingFiles(false);
    }
  }, [message]);

  useEffect(() => {
    if (fileBrowserOpen) loadFiles(currentPath);
  }, [fileBrowserOpen, currentPath, loadFiles]);

  const navigate = (path: string) => setCurrentPath(path);

  const pathSegments = currentPath === '/' ? ['/'] : ['/', ...currentPath.split('/').filter(Boolean)];
  const getSegmentPath = (idx: number): string =>
    idx === 0 ? '/' : '/' + pathSegments.slice(1, idx + 1).join('/');

  const handleCreateFile = async () => {
    const name = prompt('输入文件名：');
    if (!name?.trim()) return;
    const trimmed = name.trim();
    const filePath = joinPath(currentPath, trimmed);
    const optimistic: FileItem = { name: trimmed, path: filePath, is_dir: false };
    setFiles(prev => [...prev, optimistic].sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return a.name.localeCompare(b.name);
    }));
    try {
      await api.writeFile(filePath, '');
      message.success('文件已创建');
    } catch {
      message.error('创建失败');
      loadFiles(currentPath);
    }
  };

  const handleCreateFolder = async () => {
    const name = prompt('输入文件夹名：');
    if (!name?.trim()) return;
    const trimmed = name.trim();
    const keepPath = joinPath(currentPath, trimmed, '.gitkeep');
    const optimistic: FileItem = { name: trimmed, path: joinPath(currentPath, trimmed), is_dir: true };
    setFiles(prev => {
      const merged = [optimistic, ...prev].reduce<FileItem[]>((acc, f) => {
        if (!acc.some(x => x.path === f.path)) acc.push(f);
        return acc;
      }, []);
      return merged.sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
    });
    try {
      await api.writeFile(keepPath, '');
      message.success('文件夹已创建');
    } catch {
      message.error('创建失败');
      loadFiles(currentPath);
    }
  };

  const handleDelete = (item: FileItem) => {
    const filePath = joinPath(currentPath, item.name);
    modal.confirm({
      title: '确认删除',
      content: `确定要删除 ${item.name} 吗？`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        const snapshot = files;
        setFiles(prev => prev.filter(f => f.name !== item.name));
        try {
          await api.deleteFile(filePath);
          message.success('已删除');
        } catch {
          message.error('删除失败');
          setFiles(snapshot);
        }
      },
    });
  };

  const handleRename = async (item: FileItem) => {
    if (!renameValue.trim() || renameValue === item.name) {
      setRenaming(null);
      return;
    }
    const newName = renameValue.trim();
    const src = joinPath(currentPath, item.name);
    const dest = joinPath(currentPath, newName);
    const snapshot = files;
    setFiles(prev => prev.map(f =>
      f.name === item.name ? { ...f, name: newName, path: dest } : f
    ).sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return a.name.localeCompare(b.name);
    }));
    setRenaming(null);
    try {
      await api.moveFile(src, dest);
      message.success('重命名成功');
    } catch {
      message.error('重命名失败');
      setFiles(snapshot);
    }
  };

  const handleUpload = async (fileList: FileList | File[], keepStructure = false) => {
    const arr = Array.from(fileList);
    if (arr.length === 0) return;
    try {
      await api.uploadFiles(currentPath, arr, keepStructure);
      message.success(keepStructure ? `已上传文件夹（${arr.length} 个文件）` : `已上传 ${arr.length} 个文件`);
      loadFiles(currentPath);
    } catch {
      message.error('上传失败');
    }
  };

  const handleDownload = async (item: FileItem) => {
    const filePath = joinPath(currentPath, item.name);
    try {
      const res = await api.downloadFile(filePath);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = item.name;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败');
    }
  };

  const onDragOver = (e: DragEvent) => { e.preventDefault(); setDragActive(true); };
  const onDragLeave = () => setDragActive(false);
  const onDrop = async (e: DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const items = e.dataTransfer.items;
    if (items && items.length > 0) {
      const entries: FileSystemEntry[] = [];
      for (let i = 0; i < items.length; i++) {
        const entry = items[i].webkitGetAsEntry?.();
        if (entry) entries.push(entry);
      }
      if (entries.some(en => en.isDirectory)) {
        const allFiles = await collectFilesFromEntries(entries);
        if (allFiles.length > 0) handleUpload(allFiles, true);
        return;
      }
    }
    if (e.dataTransfer.files.length > 0) handleUpload(e.dataTransfer.files);
  };

  const handleItemClick = (item: FileItem) => {
    const path = joinPath(currentPath, item.name);
    if (item.is_dir) navigate(path);
    else openFile(path);
  };

  if (!fileBrowserOpen) return null;

  return (
    <div style={{
      width: panelWidth,
      flexShrink: 0,
      height: '100%',
      background: C.bg,
      borderLeft: `1px solid ${C.border}`,
      display: 'flex',
      flexDirection: 'column',
      position: 'relative',
    }}>
      {/* Resize handle */}
      <div
        style={{
          position: 'absolute', top: 0, left: -4, width: 8, height: '100%',
          cursor: 'col-resize', zIndex: 10,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
        onMouseDown={onResizeStart}
        onMouseEnter={() => setResizeHover(true)}
        onMouseLeave={() => setResizeHover(false)}
      >
        <div style={{
          width: 2, height: '100%', borderRadius: 1,
          background: resizeHover || draggingRef.current ? 'var(--jf-primary)' : 'transparent',
          transition: 'background 0.15s',
        }} />
      </div>

      {/* Toolbar */}
      <div style={{
        padding: '0 12px', height: 47, boxSizing: 'border-box',
        borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0,
      }}>
        {currentPath !== '/' && (
          <Tooltip title="返回上级">
            <Button type="text" size="small" icon={<ArrowLeftOutlined />}
              style={{ color: C.textSec }}
              onClick={() => navigate(currentPath.split('/').slice(0, -1).join('/') || '/')}
            />
          </Tooltip>
        )}
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4, overflow: 'hidden', minWidth: 0 }}>
          {pathSegments.map((seg, i) => (
            <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              {i > 0 && <span style={{ color: C.textDim, fontSize: 11, flexShrink: 0 }}>/</span>}
              <button
                style={{
                  color: i === pathSegments.length - 1 ? C.text : C.textSec,
                  fontSize: 12, cursor: i === pathSegments.length - 1 ? 'default' : 'pointer',
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  maxWidth: 80, background: 'none', border: 'none', padding: '2px 3px',
                  borderRadius: 'var(--jf-radius-sm)',
                }}
                onClick={() => i < pathSegments.length - 1 && navigate(getSegmentPath(i))}
              >
                {i === 0 ? <HomeOutlined /> : seg}
              </button>
            </span>
          ))}
        </div>
        <Tooltip title="刷新"><Button type="text" size="small" icon={<ReloadOutlined />} style={{ color: C.textSec }} onClick={() => loadFiles(currentPath)} /></Tooltip>
        <Tooltip title="新建文件"><Button type="text" size="small" icon={<PlusOutlined />} style={{ color: C.textSec }} onClick={handleCreateFile} /></Tooltip>
        <Tooltip title="新建文件夹"><Button type="text" size="small" icon={<FolderAddOutlined />} style={{ color: C.textSec }} onClick={handleCreateFolder} /></Tooltip>
        <Tooltip title="上传文件"><Button type="text" size="small" icon={<UploadOutlined />} style={{ color: C.textSec }} onClick={() => fileInputRef.current?.click()} /></Tooltip>
        <Tooltip title="上传文件夹"><Button type="text" size="small" icon={<FolderOpenOutlined />} style={{ color: C.textSec }} onClick={() => folderInputRef.current?.click()} /></Tooltip>
        <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }}
          onChange={(e) => { if (e.target.files) handleUpload(e.target.files); e.target.value = ''; }}
        />
        <input ref={folderInputRef} type="file" style={{ display: 'none' }}
          onChange={(e) => { if (e.target.files) handleUpload(e.target.files, true); e.target.value = ''; }}
          {...({ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
        />
        <Tooltip title="关闭面板">
          <Button type="text" size="small" icon={<CloseOutlined />}
            style={{ color: C.textSec }}
            onClick={() => setFileBrowserOpen(false)}
          />
        </Tooltip>
      </div>

      {/* File List */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0', minHeight: 0 }}>
        {loadingFiles ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, color: C.textDim, gap: 8, fontSize: 13, padding: '40px 0' }}>
            <Spin indicator={<LoadingOutlined />} />
          </div>
        ) : files.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: C.textDim, gap: 8, fontSize: 13, padding: '40px 0' }}>
            <FolderOutlined style={{ fontSize: 28 }} />
            <span>空文件夹</span>
          </div>
        ) : (
          files.map((item) => {
            const isDir = item.is_dir;
            const isRenaming = renaming === item.name;
            const isHovered = hoveredItem === item.name;
            const isActive = !isDir && editingFile === joinPath(currentPath, item.name);
            return (
              <div
                key={item.name}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px',
                  cursor: 'pointer', color: C.text, fontSize: 12,
                  borderBottom: '1px solid rgba(var(--jf-border-rgb), 0.13)',
                  transition: 'background 0.15s',
                  background: isActive ? 'rgba(var(--jf-primary-rgb), 0.12)'
                    : isHovered ? 'rgba(108, 92, 231, 0.07)' : 'transparent',
                  borderLeft: isActive ? '2px solid var(--jf-primary)' : '2px solid transparent',
                }}
                onMouseEnter={() => setHoveredItem(item.name)}
                onMouseLeave={() => setHoveredItem(null)}
                onClick={() => !isRenaming && handleItemClick(item)}
              >
                <span style={{ fontSize: 16, flexShrink: 0, width: 20, textAlign: 'center' }}>
                  {getFileIcon(item.name, isDir)}
                </span>
                {isRenaming ? (
                  <Input size="small" autoFocus value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onPressEnter={() => handleRename(item)}
                    onBlur={() => handleRename(item)}
                    onClick={(e) => e.stopPropagation()}
                    style={{ flex: 1, background: C.bgDark, borderColor: C.accent, color: C.text }}
                  />
                ) : (
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {item.name}
                  </span>
                )}
                {!isDir && <span style={{ color: C.textDim, fontSize: 10, flexShrink: 0 }}>{formatSize(item.size)}</span>}
                {isHovered && !isRenaming && (
                  <div style={{ display: 'flex', gap: 2, flexShrink: 0 }}>
                    <Tooltip title="重命名">
                      <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 11 }} />}
                        style={{ color: C.textDim, width: 22, height: 22, minWidth: 22 }}
                        onClick={(e) => { e.stopPropagation(); setRenaming(item.name); setRenameValue(item.name); }}
                      />
                    </Tooltip>
                    {!isDir && (
                      <Tooltip title="下载">
                        <Button type="text" size="small" icon={<DownloadOutlined style={{ fontSize: 11 }} />}
                          style={{ color: C.textDim, width: 22, height: 22, minWidth: 22 }}
                          onClick={(e) => { e.stopPropagation(); handleDownload(item); }}
                        />
                      </Tooltip>
                    )}
                    <Tooltip title="删除">
                      <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 11 }} />}
                        style={{ color: C.danger, width: 22, height: 22, minWidth: 22 }}
                        onClick={(e) => { e.stopPropagation(); handleDelete(item); }}
                      />
                    </Tooltip>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Drop Zone */}
      <div
        style={{
          padding: '12px 10px', borderTop: `1px solid ${C.border}`,
          background: dragActive ? 'rgba(108, 92, 231, 0.09)' : 'transparent',
          border: dragActive ? `2px dashed ${C.accent}` : `2px dashed ${C.border}`,
          borderRadius: 0, margin: '0 10px 10px',
          borderBottomLeftRadius: 'var(--jf-radius-md)', borderBottomRightRadius: 'var(--jf-radius-md)',
          textAlign: 'center', color: C.textDim, fontSize: 11, cursor: 'pointer',
          transition: 'all 0.2s',
        }}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
      >
        <UploadOutlined style={{ fontSize: 16, marginBottom: 2 }} />
        <div>拖拽上传</div>
      </div>
    </div>
  );
}

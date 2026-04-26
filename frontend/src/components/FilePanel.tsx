import {
  useState, useEffect, useCallback, useMemo, useRef,
  type DragEvent, type MouseEvent as ReactMouseEvent,
  type ClipboardEvent, type KeyboardEvent,
} from 'react';
import { Button, Input, Tooltip, Spin, App, Dropdown, Progress } from 'antd';
import type { MenuProps } from 'antd';
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
  SortAscendingOutlined,
  ScissorOutlined,
  CopyOutlined,
  SnippetsOutlined,
  ExportOutlined,
  FileZipOutlined,
} from '@ant-design/icons';
import * as api from '../services/api';
import type { FileItem } from '../types';
import { useFileWorkspace } from '../stores/fileWorkspaceContext';
import FolderPicker from './FolderPicker';

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

function formatMtimeShort(iso?: string): string {
  if (!iso) return '';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return iso.slice(0, 16);
  return `${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
}

type SortKey = 'name-asc' | 'name-desc' | 'mtime-desc' | 'mtime-asc' | 'size-desc' | 'size-asc';

const SORT_STORAGE_KEY = 'jf-filepanel-sort';

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'name-asc', label: '名称 A→Z' },
  { key: 'name-desc', label: '名称 Z→A' },
  { key: 'mtime-desc', label: '修改时间 新→旧' },
  { key: 'mtime-asc', label: '修改时间 旧→新' },
  { key: 'size-desc', label: '大小 大→小' },
  { key: 'size-asc', label: '大小 小→大' },
];

function sortFiles(items: FileItem[], sortKey: SortKey): FileItem[] {
  const cmpName = (a: FileItem, b: FileItem) => a.name.localeCompare(b.name);
  const cmpMtime = (a: FileItem, b: FileItem) => (a.modified_at || '').localeCompare(b.modified_at || '');
  const cmpSize = (a: FileItem, b: FileItem) => (a.size ?? 0) - (b.size ?? 0);
  const [field, dir] = sortKey.split('-') as ['name' | 'mtime' | 'size', 'asc' | 'desc'];
  const base = field === 'name' ? cmpName : field === 'mtime' ? cmpMtime : cmpSize;
  const sign = dir === 'desc' ? -1 : 1;
  return [...items].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    return sign * base(a, b);
  });
}

function joinPath(...parts: string[]): string {
  return parts.join('/').replace(/\/+/g, '/').replace(/\/$/, '') || '/';
}

function parentDir(path: string): string {
  if (!path || path === '/') return '/';
  const trimmed = path.replace(/\/+$/, '');
  const idx = trimmed.lastIndexOf('/');
  if (idx <= 0) return '/';
  return trimmed.slice(0, idx);
}

function basename(path: string): string {
  const trimmed = path.replace(/\/+$/, '');
  const idx = trimmed.lastIndexOf('/');
  return idx >= 0 ? trimmed.slice(idx + 1) : trimmed;
}

const C = {
  bg: 'var(--jf-bg-panel)',
  bgDark: 'var(--jf-bg-deep)',
  border: 'var(--jf-border)',
  text: 'var(--jf-text)',
  textSec: 'var(--jf-text-muted)',
  textDim: 'var(--jf-text-dim)',
  accent: 'var(--jf-legacy)',
  primary: 'var(--jf-primary)',
  danger: '#e74c3c',
};

// ── External-drop helpers (drag a file/folder from OS into panel) ─────

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

// ── Internal drag/drop MIME ───────────────────────────────────────────
//
// The drag carries a JSON array of absolute paths (FilePanel's own items)
// so an item drag can be told apart from an OS file drag (which carries
// the well-known `Files` type instead). We deliberately use a custom MIME
// rather than relying on dataTransfer.items because Chrome on Windows
// drops the file-payload mime entirely for OS drags from File Explorer
// only on the FIRST cross-app drag of a session.
const INTERNAL_DRAG_MIME = 'application/x-jf-file-paths';

const MIN_W = 220;
const MAX_W = 420;
const DEFAULT_W = 280;

interface ClipboardState {
  paths: string[];
  mode: 'copy' | 'cut';
  /** Visual hint: dim the icons of items in the cut list. */
  sourceDir: string;
}

export default function FilePanel() {
  const { message, modal } = App.useApp();
  const {
    fileBrowserOpen, setFileBrowserOpen, editingFile, openFile,
    browserPath: currentPath, setBrowserPath: setCurrentPath,
  } = useFileWorkspace();

  const [files, setFiles] = useState<FileItem[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>(() => {
    if (typeof window === 'undefined') return 'name-asc';
    const saved = window.localStorage.getItem(SORT_STORAGE_KEY) as SortKey | null;
    return saved && SORT_OPTIONS.some(o => o.key === saved) ? saved : 'name-asc';
  });
  useEffect(() => {
    try { window.localStorage.setItem(SORT_STORAGE_KEY, sortKey); } catch { /* ignore quota */ }
  }, [sortKey]);
  const displayedFiles = useMemo(() => sortFiles(files, sortKey), [files, sortKey]);

  // ── selection state (Finder-style) ─────────────────────────────────
  // `selected` holds the basename relative to currentPath. Switching dirs
  // clears it (the items disappear anyway). Multi-selection drives the
  // batch toolbar at the top of the list and the keyboard clipboard.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastSelected, setLastSelected] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [hoveredItem, setHoveredItem] = useState<string | null>(null);
  const [dragOverItem, setDragOverItem] = useState<string | null>(null);
  const [externalDragActive, setExternalDragActive] = useState(false);
  const [resizeHover, setResizeHover] = useState(false);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_W);
  const [folderPickerOpen, setFolderPickerOpen] = useState<{
    paths: string[];
    mode: 'copy' | 'move';
  } | null>(null);
  const [clipboard, setClipboard] = useState<ClipboardState | null>(null);
  /** Upload-in-progress overlay state. Shown as a sticky banner above the file
   *  list whenever an upload is running. `keepStructure` indicates folder upload
   *  (changes wording from "上传文件" → "上传文件夹"). */
  const [uploadProgress, setUploadProgress] = useState<{
    fileCount: number;
    cumulativeLoaded: number;
    cumulativeTotal: number;
    batchIndex: number;
    batchCount: number;
    currentFileName: string;
    keepStructure: boolean;
    targetDir: string;
  } | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const draggingRef = useRef(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const selectedItems = useMemo(
    () => displayedFiles.filter((f) => selected.has(f.name)),
    [displayedFiles, selected],
  );
  const selectedAbsPaths = useMemo(
    () => selectedItems.map((f) => joinPath(currentPath, f.name)),
    [selectedItems, currentPath],
  );

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
      setFiles(items);
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

  // Switching directory clears multi-selection (items no longer visible).
  useEffect(() => {
    setSelected(new Set());
    setLastSelected(null);
  }, [currentPath]);

  const navigate = (path: string) => setCurrentPath(path);

  const pathSegments = currentPath === '/' ? ['/'] : ['/', ...currentPath.split('/').filter(Boolean)];
  const getSegmentPath = (idx: number): string =>
    idx === 0 ? '/' : '/' + pathSegments.slice(1, idx + 1).join('/');

  // ── single-item ops (kept for header buttons + right-click menu) ───

  const handleCreateFile = async () => {
    const name = prompt('输入文件名：');
    if (!name?.trim()) return;
    const trimmed = name.trim();
    const filePath = joinPath(currentPath, trimmed);
    const optimistic: FileItem = { name: trimmed, path: filePath, is_dir: false };
    setFiles(prev => [...prev, optimistic]);
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
      return merged;
    });
    try {
      await api.writeFile(keepPath, '');
      message.success('文件夹已创建');
    } catch {
      message.error('创建失败');
      loadFiles(currentPath);
    }
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
    ));
    setRenaming(null);
    try {
      await api.moveFile(src, dest);
      message.success('重命名成功');
    } catch {
      message.error('重命名失败');
      setFiles(snapshot);
    }
  };

  /** Common entry for all upload paths (button click, drag-into-panel,
   *  drag-into-folder-row, system clipboard paste). Wraps the XHR-based
   *  uploader with a sticky progress banner and cumulative byte tracking. */
  const runUploadTo = useCallback(async (
    targetDir: string,
    fileList: FileList | File[],
    keepStructure: boolean,
  ) => {
    const arr = Array.from(fileList);
    if (arr.length === 0) return;

    const cumulativeTotal = arr.reduce((s, f) => s + (f.size || 0), 0);
    setUploadProgress({
      fileCount: arr.length,
      cumulativeLoaded: 0,
      cumulativeTotal,
      batchIndex: 0,
      batchCount: 1,
      currentFileName: arr[0]?.name || '',
      keepStructure,
      targetDir,
    });

    // rAF-throttle UI updates so a 1000-tick xhr.upload.onprogress on
    // localhost doesn't drown React in setState calls.
    let pendingTick: api.UploadProgressEvent | null = null;
    let rafScheduled = false;
    const flushTick = () => {
      rafScheduled = false;
      if (!pendingTick) return;
      const t = pendingTick;
      pendingTick = null;
      setUploadProgress((prev) => prev && {
        ...prev,
        cumulativeLoaded: t.cumulativeLoaded,
        cumulativeTotal: t.cumulativeTotal,
        batchIndex: t.batchIndex,
        batchCount: t.batchCount,
        currentFileName: t.currentFileName,
        fileCount: t.fileCount,
      });
    };
    const onProgress = (e: api.UploadProgressEvent) => {
      pendingTick = e;
      if (!rafScheduled) {
        rafScheduled = true;
        requestAnimationFrame(flushTick);
      }
    };

    try {
      await api.uploadFilesWithProgress(targetDir, arr, keepStructure, onProgress);
      message.success(
        keepStructure
          ? `已上传文件夹（${arr.length} 个文件）`
          : `已上传 ${arr.length} 个文件`
      );
      if (targetDir === currentPath) loadFiles(currentPath);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '上传失败';
      message.error(msg);
    } finally {
      // Small delay so the user sees 100% briefly before the banner disappears.
      window.setTimeout(() => setUploadProgress(null), 400);
    }
  }, [currentPath, message]);

  const handleUpload = (fileList: FileList | File[], keepStructure = false) =>
    runUploadTo(currentPath, fileList, keepStructure);

  // ── batch ops ──────────────────────────────────────────────────────

  const handleDeletePaths = useCallback((paths: string[]) => {
    if (paths.length === 0) return;
    const label = paths.length === 1 ? basename(paths[0]) : `${paths.length} 项`;
    modal.confirm({
      title: '确认删除',
      content: `确定要删除 ${label} 吗？此操作不可撤销。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        const names = paths.map(basename);
        const snapshot = files;
        setFiles(prev => prev.filter(f => !names.includes(f.name)));
        setSelected((prev) => {
          const next = new Set(prev);
          names.forEach(n => next.delete(n));
          return next;
        });
        try {
          // Keep API serial to avoid hammering the storage backend on huge
          // selections; user-perceptible since we already updated the UI.
          for (const p of paths) {
            await api.deleteFile(p);
          }
          message.success(`已删除 ${paths.length} 项`);
        } catch {
          message.error('删除失败');
          setFiles(snapshot);
        }
      },
    });
  }, [files, modal, message]);

  const handleDownloadPath = useCallback(async (filePath: string, name: string) => {
    try {
      const res = await api.downloadFile(filePath);
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
  }, [message]);

  const handleZipDownload = useCallback((paths: string[]) => {
    if (paths.length === 0) return;
    // Plain anchor click — the server response sets Content-Disposition,
    // so the browser saves rather than navigates. Cross-tab safer than
    // window.open() (which can be popup-blocked).
    const a = document.createElement('a');
    a.href = api.zipDownloadUrl(paths);
    a.click();
    message.success(paths.length === 1 ? '开始下载 zip' : `开始打包 ${paths.length} 项为 zip`);
  }, [message]);

  /** Move OR copy a list of source paths into the given destination dir,
   *  one by one, handling 「目标已存在」 collisions interactively.
   *
   *  Why per-item prompt instead of bulk-overwrite or skip-all:
   *  for the common case (small selections), one prompt is fine; for huge
   *  selections, the user can hit Esc on the first prompt and we abort.
   *  An "apply to all" toggle would be nicer but adds modal state for a
   *  feature that's used <10x/day. */
  const performBulkMoveOrCopy = useCallback(async (
    sourcePaths: string[], destDir: string, mode: 'move' | 'copy',
  ): Promise<void> => {
    if (sourcePaths.length === 0) return;
    const op = mode === 'move' ? api.moveFile : api.copyFile;
    const opLabel = mode === 'move' ? '移动' : '复制';
    let successCount = 0;
    let aborted = false;
    for (const src of sourcePaths) {
      const name = basename(src);
      let target = joinPath(destDir, name);
      if (target === src) {
        // Cut-and-paste back to the same dir is a no-op (Finder behaviour).
        if (mode === 'move') continue;
        // Copy to same dir → suffix " 副本".
        target = joinPath(destDir, suffixCopy(name));
      }
      try {
        await op(src, target);
        successCount++;
      } catch (e) {
        const detail = (e as { message?: string })?.message || '';
        if (!detail.includes('目标路径已存在')) {
          message.error(`${opLabel}失败：${name}`);
          continue;
        }
        // Conflict: ask user for a new name.
        const newName = prompt(`「${name}」在目标位置已存在，输入新名称（取消则跳过）`, suffixCopy(name));
        if (!newName?.trim()) {
          if (sourcePaths.length > 1) {
            const cont = window.confirm(`已跳过「${name}」，继续处理剩余 ${sourcePaths.length - successCount - 1} 项？`);
            if (!cont) { aborted = true; break; }
          }
          continue;
        }
        try {
          await op(src, joinPath(destDir, newName.trim()));
          successCount++;
        } catch {
          message.error(`${opLabel}失败：${name}`);
        }
      }
    }
    if (successCount > 0) {
      message.success(`${opLabel}成功 ${successCount} 项`);
    }
    if (aborted) {
      message.info('已取消剩余操作');
    }
    loadFiles(currentPath);
  }, [currentPath, loadFiles, message]);

  // ── clipboard (virtual) ────────────────────────────────────────────

  const cutSelection = useCallback(() => {
    if (selectedAbsPaths.length === 0) return;
    setClipboard({ paths: selectedAbsPaths, mode: 'cut', sourceDir: currentPath });
    message.info(`已剪切 ${selectedAbsPaths.length} 项，去目标文件夹按 Ctrl+V`);
  }, [selectedAbsPaths, currentPath, message]);

  const copySelection = useCallback(() => {
    if (selectedAbsPaths.length === 0) return;
    setClipboard({ paths: selectedAbsPaths, mode: 'copy', sourceDir: currentPath });
    message.info(`已复制 ${selectedAbsPaths.length} 项，去目标文件夹按 Ctrl+V`);
  }, [selectedAbsPaths, currentPath, message]);

  const pasteHere = useCallback(async () => {
    if (!clipboard || clipboard.paths.length === 0) return;
    const mode = clipboard.mode === 'cut' ? 'move' : 'copy';
    await performBulkMoveOrCopy(clipboard.paths, currentPath, mode);
    if (clipboard.mode === 'cut') {
      setClipboard(null);
    }
  }, [clipboard, currentPath, performBulkMoveOrCopy]);

  // ── selection helpers ──────────────────────────────────────────────

  const handleItemActivate = useCallback((item: FileItem) => {
    // "Open" semantics: same as before — folder navigates, file opens.
    const path = joinPath(currentPath, item.name);
    if (item.is_dir) navigate(path);
    else openFile(path);
  }, [currentPath, openFile]);
  // ↑ navigate is stable (just a state setter), no need to depend on it

  const handleItemClick = useCallback((e: ReactMouseEvent, item: FileItem) => {
    if (renaming) return;
    const isMod = e.ctrlKey || e.metaKey;
    const isShift = e.shiftKey;
    if (isShift && lastSelected) {
      // Range select between lastSelected and current item (in displayed order).
      const names = displayedFiles.map(f => f.name);
      const a = names.indexOf(lastSelected);
      const b = names.indexOf(item.name);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        const range = names.slice(lo, hi + 1);
        setSelected(new Set(range));
      }
      return;
    }
    if (isMod) {
      setSelected(prev => {
        const next = new Set(prev);
        if (next.has(item.name)) next.delete(item.name);
        else next.add(item.name);
        return next;
      });
      setLastSelected(item.name);
      return;
    }
    // Plain click: select-only (clear others) AND activate (open / navigate).
    setSelected(new Set([item.name]));
    setLastSelected(item.name);
    handleItemActivate(item);
  }, [renaming, lastSelected, displayedFiles, handleItemActivate]);

  const clearSelection = useCallback(() => {
    setSelected(new Set());
    setLastSelected(null);
  }, []);

  // ── keyboard shortcuts ─────────────────────────────────────────────
  // Bound on the panel root with onKeyDown so they only fire when the panel
  // (or one of its descendants) has focus. Avoids hijacking Ctrl+C in chat
  // or other panels — a common source of frustration.

  const handlePanelKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (renaming) return;
    const target = e.target as HTMLElement;
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return;
    const isMod = e.ctrlKey || e.metaKey;

    if (e.key === 'Escape') {
      if (selected.size > 0 || clipboard) {
        e.preventDefault();
        clearSelection();
        if (clipboard) setClipboard(null);
      }
      return;
    }
    if (isMod && e.key.toLowerCase() === 'a') {
      e.preventDefault();
      setSelected(new Set(displayedFiles.map(f => f.name)));
      return;
    }
    if (isMod && e.key.toLowerCase() === 'c' && selected.size > 0) {
      e.preventDefault();
      copySelection();
      return;
    }
    if (isMod && e.key.toLowerCase() === 'x' && selected.size > 0) {
      e.preventDefault();
      cutSelection();
      return;
    }
    if (isMod && e.key.toLowerCase() === 'v' && clipboard) {
      e.preventDefault();
      void pasteHere();
      return;
    }
    if ((e.key === 'Delete' || e.key === 'Backspace') && selected.size > 0) {
      e.preventDefault();
      handleDeletePaths(selectedAbsPaths);
    }
  }, [
    renaming, selected, clipboard, displayedFiles, selectedAbsPaths,
    clearSelection, copySelection, cutSelection, pasteHere, handleDeletePaths,
  ]);

  // ── drag-and-drop: external upload + internal move ─────────────────

  const onItemDragStart = useCallback((e: DragEvent, item: FileItem) => {
    if (renaming) { e.preventDefault(); return; }
    // If the user starts dragging an item not already selected, treat the
    // drag as a single-item drag (matches Finder/Explorer; avoids surprise
    // when user casually grabs a folder while another set is selected).
    let paths: string[];
    if (selected.has(item.name) && selected.size > 1) {
      paths = selectedAbsPaths;
    } else {
      paths = [joinPath(currentPath, item.name)];
      setSelected(new Set([item.name]));
      setLastSelected(item.name);
    }
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData(INTERNAL_DRAG_MIME, JSON.stringify(paths));
    // Empty text/plain so OS file managers don't try to interpret the drag
    // as a text selection (Windows Explorer otherwise creates a .txt file).
    e.dataTransfer.setData('text/plain', '');
  }, [renaming, selected, selectedAbsPaths, currentPath]);

  const isInternalDrag = (e: DragEvent): boolean => (
    Array.from(e.dataTransfer.types).includes(INTERNAL_DRAG_MIME)
  );

  const onItemDragOver = (e: DragEvent, item: FileItem) => {
    if (!item.is_dir) return;
    if (!isInternalDrag(e) && !e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = isInternalDrag(e) ? 'move' : 'copy';
    setDragOverItem(item.name);
  };

  const onItemDragLeave = () => setDragOverItem(null);

  const onItemDrop = async (e: DragEvent, item: FileItem) => {
    if (!item.is_dir) return;
    e.preventDefault();
    e.stopPropagation();
    setDragOverItem(null);
    const target = joinPath(currentPath, item.name);
    if (isInternalDrag(e)) {
      const raw = e.dataTransfer.getData(INTERNAL_DRAG_MIME);
      try {
        const sources: string[] = JSON.parse(raw);
        // Forbid dragging a folder into itself (or its descendants).
        const filtered = sources.filter(s => target !== s && !target.startsWith(s + '/'));
        if (filtered.length === 0) {
          message.warning('不能把文件夹拖到自己里面');
          return;
        }
        await performBulkMoveOrCopy(filtered, target, 'move');
      } catch {
        /* malformed payload */
      }
      return;
    }
    // External upload into this folder.
    const items = e.dataTransfer.items;
    if (items && items.length > 0) {
      const entries: FileSystemEntry[] = [];
      for (let i = 0; i < items.length; i++) {
        const entry = items[i].webkitGetAsEntry?.();
        if (entry) entries.push(entry);
      }
      if (entries.some(en => en.isDirectory)) {
        const allFiles = await collectFilesFromEntries(entries);
        if (allFiles.length > 0) {
          await runUploadTo(target, allFiles, true);
        }
        return;
      }
    }
    if (e.dataTransfer.files.length > 0) {
      await runUploadTo(target, Array.from(e.dataTransfer.files), false);
    }
  };

  // ── breadcrumb / parent-button drop targets (move into ../) ────────

  const onCrumbDragOver = (e: DragEvent) => {
    if (!isInternalDrag(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const onCrumbDrop = async (e: DragEvent, targetPath: string) => {
    if (!isInternalDrag(e)) return;
    e.preventDefault();
    const raw = e.dataTransfer.getData(INTERNAL_DRAG_MIME);
    try {
      const sources: string[] = JSON.parse(raw);
      const filtered = sources.filter(s => parentDir(s) !== targetPath);
      if (filtered.length === 0) return;
      await performBulkMoveOrCopy(filtered, targetPath, 'move');
    } catch { /* malformed */ }
  };

  // ── external drop into the panel root (upload to currentPath) ──────

  const onPanelDragOver = (e: DragEvent) => {
    if (isInternalDrag(e)) return;
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    setExternalDragActive(true);
  };
  const onPanelDragLeave = (e: DragEvent) => {
    // Only deactivate when leaving the panel entirely (relatedTarget is
    // null when the cursor exits the window or drops on a non-handled area).
    const related = e.relatedTarget as Node | null;
    if (!related || !panelRef.current?.contains(related)) {
      setExternalDragActive(false);
    }
  };
  const onPanelDrop = async (e: DragEvent) => {
    if (isInternalDrag(e)) return;
    e.preventDefault();
    setExternalDragActive(false);
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

  // ── system clipboard paste (image / file) ──────────────────────────
  // Note: only fires when the panel itself has focus (we tabIndex=0 it).
  // The virtual clipboard takes precedence via Ctrl+V keydown handler;
  // this onPaste only activates when no virtual clipboard is set OR the
  // user pasted a real file/image (clipboardData.files has entries).
  const onPanelPaste = (e: ClipboardEvent<HTMLDivElement>) => {
    const filesFromClipboard = Array.from(e.clipboardData.files || []);
    if (filesFromClipboard.length > 0) {
      e.preventDefault();
      // Auto-name pasted images so the FilePanel gets a stable filename
      // rather than the OS-default 「image.png」 every time (which would
      // collide on every paste).
      const renamed = filesFromClipboard.map((f) => {
        if (f.name && f.name !== 'image.png') return f;
        const ts = new Date().toISOString().replace(/[:.]/g, '-');
        const ext = f.type.split('/')[1] || 'png';
        return new File([f], `pasted-${ts}.${ext}`, { type: f.type });
      });
      handleUpload(renamed);
    }
    // No files: let the virtual clipboard handler (Ctrl+V → pasteHere)
    // own this event. It runs from onKeyDown, not onPaste.
  };

  // ── right-click context menu ───────────────────────────────────────

  const buildContextMenu = (item: FileItem | null): MenuProps['items'] => {
    const isMulti = selected.size > 1 && (item == null || selected.has(item.name));
    const targets: { paths: string[]; label: string } = isMulti
      ? { paths: selectedAbsPaths, label: `${selectedAbsPaths.length} 项` }
      : item
      ? { paths: [joinPath(currentPath, item.name)], label: item.name }
      : { paths: [], label: '' };
    if (targets.paths.length === 0) return [];
    return [
      {
        key: 'open', icon: <FolderOpenOutlined />, label: '打开',
        disabled: isMulti,
        onClick: () => item && handleItemActivate(item),
      },
      {
        key: 'rename', icon: <EditOutlined />, label: '重命名',
        disabled: isMulti || !item,
        onClick: () => { if (item) { setRenaming(item.name); setRenameValue(item.name); } },
      },
      { type: 'divider' as const },
      {
        key: 'copy', icon: <CopyOutlined />, label: '复制 (Ctrl+C)',
        onClick: () => {
          // Right-click on an unselected item should target only that item.
          if (item && !selected.has(item.name)) {
            setSelected(new Set([item.name]));
            setLastSelected(item.name);
            setClipboard({ paths: [joinPath(currentPath, item.name)], mode: 'copy', sourceDir: currentPath });
            message.info('已复制 1 项，去目标文件夹按 Ctrl+V');
          } else {
            copySelection();
          }
        },
      },
      {
        key: 'cut', icon: <ScissorOutlined />, label: '剪切 (Ctrl+X)',
        onClick: () => {
          if (item && !selected.has(item.name)) {
            setSelected(new Set([item.name]));
            setLastSelected(item.name);
            setClipboard({ paths: [joinPath(currentPath, item.name)], mode: 'cut', sourceDir: currentPath });
            message.info('已剪切 1 项，去目标文件夹按 Ctrl+V');
          } else {
            cutSelection();
          }
        },
      },
      {
        key: 'paste', icon: <SnippetsOutlined />, label: '粘贴 (Ctrl+V)',
        disabled: !clipboard,
        onClick: () => void pasteHere(),
      },
      { type: 'divider' as const },
      {
        key: 'send-to', icon: <ExportOutlined />, label: '发送到…',
        onClick: () => setFolderPickerOpen({ paths: targets.paths, mode: 'move' }),
      },
      {
        key: 'copy-to', icon: <CopyOutlined />, label: '复制到…',
        onClick: () => setFolderPickerOpen({ paths: targets.paths, mode: 'copy' }),
      },
      { type: 'divider' as const },
      {
        key: 'download', icon: <DownloadOutlined />, label: isMulti || (item && item.is_dir) ? '打包下载 (zip)' : '下载',
        onClick: () => {
          if (isMulti || (item && item.is_dir)) {
            handleZipDownload(targets.paths);
          } else if (item) {
            void handleDownloadPath(joinPath(currentPath, item.name), item.name);
          }
        },
      },
      {
        key: 'zip', icon: <FileZipOutlined />, label: '打包为 zip 下载',
        disabled: !isMulti && !!(item && !item.is_dir) ? false : false,
        onClick: () => handleZipDownload(targets.paths),
      },
      { type: 'divider' as const },
      {
        key: 'delete', icon: <DeleteOutlined style={{ color: C.danger }} />,
        label: <span style={{ color: C.danger }}>删除 (Del)</span>,
        onClick: () => handleDeletePaths(targets.paths),
      },
    ];
  };

  if (!fileBrowserOpen) return null;

  return (
    <div
      ref={panelRef}
      tabIndex={0}
      onKeyDown={handlePanelKeyDown}
      onPaste={onPanelPaste}
      onDragOver={onPanelDragOver}
      onDragLeave={onPanelDragLeave}
      onDrop={onPanelDrop}
      style={{
        width: panelWidth,
        flexShrink: 0,
        height: '100%',
        background: C.bg,
        borderLeft: `1px solid ${C.border}`,
        display: 'flex',
        flexDirection: 'column',
        position: 'relative',
        outline: 'none',
      }}
    >
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
          background: resizeHover || draggingRef.current ? C.primary : 'transparent',
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
          <Tooltip title="返回上级（也可拖文件到这里 → 移到上一级）">
            <Button
              type="text" size="small" icon={<ArrowLeftOutlined />}
              style={{ color: C.textSec }}
              onClick={() => navigate(parentDir(currentPath))}
              onDragOver={onCrumbDragOver}
              onDrop={(e) => onCrumbDrop(e, parentDir(currentPath))}
            />
          </Tooltip>
        )}
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4, overflow: 'hidden', minWidth: 0 }}>
          {pathSegments.map((seg, i) => {
            const isLast = i === pathSegments.length - 1;
            const segPath = getSegmentPath(i);
            return (
              <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                {i > 0 && <span style={{ color: C.textDim, fontSize: 11, flexShrink: 0 }}>/</span>}
                <button
                  style={{
                    color: isLast ? C.text : C.textSec,
                    fontSize: 12, cursor: isLast ? 'default' : 'pointer',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                    maxWidth: 80, background: 'none', border: 'none', padding: '2px 3px',
                    borderRadius: 'var(--jf-radius-sm)',
                  }}
                  onClick={() => !isLast && navigate(segPath)}
                  onDragOver={onCrumbDragOver}
                  onDrop={(e) => onCrumbDrop(e, segPath)}
                >
                  {i === 0 ? <HomeOutlined /> : seg}
                </button>
              </span>
            );
          })}
        </div>
        <Tooltip title="刷新"><Button type="text" size="small" icon={<ReloadOutlined />} style={{ color: C.textSec }} onClick={() => loadFiles(currentPath)} /></Tooltip>
        <Dropdown
          trigger={['click']}
          menu={{
            selectedKeys: [sortKey],
            items: SORT_OPTIONS.map(o => ({ key: o.key, label: o.label })),
            onClick: ({ key }) => setSortKey(key as SortKey),
          }}
        >
          <Tooltip title={`排序：${SORT_OPTIONS.find(o => o.key === sortKey)?.label ?? ''}`}>
            <Button type="text" size="small" icon={<SortAscendingOutlined />} style={{ color: C.textSec }} />
          </Tooltip>
        </Dropdown>
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

      {/* Batch action bar — appears once anything is multi-selected.
          Sits above the file list so it doesn't shift list items. */}
      {selected.size > 0 && (
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '6px 12px', flexShrink: 0,
            background: 'rgba(var(--jf-primary-rgb), 0.10)',
            borderBottom: `1px solid ${C.border}`,
            fontSize: 12, color: C.text,
          }}
        >
          <span>已选 {selected.size} 项</span>
          <div style={{ flex: 1 }} />
          <Tooltip title="复制 (Ctrl+C)">
            <Button type="text" size="small" icon={<CopyOutlined />} onClick={copySelection} style={{ color: C.textSec }} />
          </Tooltip>
          <Tooltip title="剪切 (Ctrl+X)">
            <Button type="text" size="small" icon={<ScissorOutlined />} onClick={cutSelection} style={{ color: C.textSec }} />
          </Tooltip>
          <Tooltip title="发送到…">
            <Button type="text" size="small" icon={<ExportOutlined />}
              onClick={() => setFolderPickerOpen({ paths: selectedAbsPaths, mode: 'move' })}
              style={{ color: C.textSec }}
            />
          </Tooltip>
          <Tooltip title="打包 zip 下载">
            <Button type="text" size="small" icon={<FileZipOutlined />}
              onClick={() => handleZipDownload(selectedAbsPaths)}
              style={{ color: C.textSec }}
            />
          </Tooltip>
          <Tooltip title="删除 (Del)">
            <Button type="text" size="small" icon={<DeleteOutlined />}
              onClick={() => handleDeletePaths(selectedAbsPaths)}
              style={{ color: C.danger }}
            />
          </Tooltip>
        </div>
      )}

      {/* Upload progress banner — sticky above file list while uploading.
          Shows file count, target dir, current file (if known), and a Progress
          bar driven by xhr.upload.onprogress (rAF-throttled in runUploadTo). */}
      {uploadProgress && (
        <div
          style={{
            display: 'flex', flexDirection: 'column', gap: 4,
            padding: '6px 12px 8px', flexShrink: 0,
            background: 'rgba(232, 159, 217, 0.08)',
            borderBottom: `1px solid ${C.border}`,
            fontSize: 11, color: C.textSec,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <LoadingOutlined style={{ color: C.primary, fontSize: 12 }} spin />
            <span style={{ color: C.text, fontWeight: 500 }}>
              {uploadProgress.keepStructure ? '上传文件夹中…' : '上传中…'}
            </span>
            <span>· {uploadProgress.fileCount} 个文件</span>
            {uploadProgress.batchCount > 1 && (
              <span>· 第 {uploadProgress.batchIndex}/{uploadProgress.batchCount} 批</span>
            )}
            <div style={{ flex: 1 }} />
            <span style={{ color: C.textDim }}>
              {formatSize(uploadProgress.cumulativeLoaded)} / {formatSize(uploadProgress.cumulativeTotal)}
            </span>
          </div>
          <Progress
            percent={
              uploadProgress.cumulativeTotal > 0
                ? Math.round((uploadProgress.cumulativeLoaded / uploadProgress.cumulativeTotal) * 100)
                : 0
            }
            size="small"
            showInfo={false}
            strokeColor={C.primary}
            trailColor={C.border}
            style={{ marginBottom: 0, lineHeight: 1 }}
          />
          {uploadProgress.currentFileName && (
            <div
              style={{
                color: C.textDim, fontSize: 10,
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}
              title={`${uploadProgress.targetDir} · ${uploadProgress.currentFileName}`}
            >
              → {uploadProgress.currentFileName}
            </div>
          )}
        </div>
      )}

      {/* Clipboard hint bar — shown when something is on the virtual clipboard. */}
      {clipboard && (
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '4px 12px', flexShrink: 0,
            background: 'rgba(95, 201, 230, 0.08)',
            borderBottom: `1px solid ${C.border}`,
            fontSize: 11, color: C.textSec,
          }}
        >
          {clipboard.mode === 'cut' ? <ScissorOutlined /> : <CopyOutlined />}
          <span>剪贴板：{clipboard.paths.length} 项 · 来自 {clipboard.sourceDir}</span>
          <div style={{ flex: 1 }} />
          <Button type="text" size="small" onClick={pasteHere} style={{ color: C.primary, height: 20, padding: '0 6px' }}>粘贴到此</Button>
          <Button type="text" size="small" onClick={() => setClipboard(null)} style={{ color: C.textDim, height: 20, padding: '0 6px' }}>清空</Button>
        </div>
      )}

      {/* File List */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0', minHeight: 0 }}
        onClick={(e) => {
          // Background click clears selection (Finder behaviour). Skip if
          // the click bubbled from a row (rows already stopPropagation).
          if (e.target === e.currentTarget) clearSelection();
        }}
      >
        {loadingFiles ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, color: C.textDim, gap: 8, fontSize: 13, padding: '40px 0' }}>
            <Spin indicator={<LoadingOutlined />} />
          </div>
        ) : displayedFiles.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: C.textDim, gap: 8, fontSize: 13, padding: '40px 0' }}>
            <FolderOutlined style={{ fontSize: 28 }} />
            <span>空文件夹</span>
          </div>
        ) : (
          displayedFiles.map((item) => {
            const isDir = item.is_dir;
            const itemAbsPath = joinPath(currentPath, item.name);
            const isRenaming = renaming === item.name;
            const isHovered = hoveredItem === item.name;
            const isActive = !isDir && editingFile === itemAbsPath;
            const isSelected = selected.has(item.name);
            const isDragOver = dragOverItem === item.name;
            const isCut = clipboard?.mode === 'cut' && clipboard.paths.includes(itemAbsPath);

            const background = isDragOver
              ? 'rgba(var(--jf-primary-rgb), 0.25)'
              : isSelected
              ? 'rgba(var(--jf-primary-rgb), 0.18)'
              : isActive
              ? 'rgba(var(--jf-primary-rgb), 0.10)'
              : isHovered
              ? 'rgba(108, 92, 231, 0.07)'
              : 'transparent';

            const borderLeft = isDragOver
              ? `2px solid ${C.primary}`
              : isSelected
              ? `2px solid ${C.primary}`
              : isActive
              ? `2px solid ${C.primary}`
              : '2px solid transparent';

            const row = (
              <div
                key={item.name}
                draggable={!isRenaming}
                onDragStart={(e) => onItemDragStart(e, item)}
                onDragOver={(e) => onItemDragOver(e, item)}
                onDragLeave={onItemDragLeave}
                onDrop={(e) => onItemDrop(e, item)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px',
                  cursor: 'pointer', color: C.text, fontSize: 12,
                  borderBottom: '1px solid rgba(var(--jf-border-rgb), 0.13)',
                  transition: 'background 0.15s, border-left-color 0.15s',
                  background, borderLeft,
                  opacity: isCut ? 0.55 : 1,
                  userSelect: 'none',
                }}
                onMouseEnter={() => setHoveredItem(item.name)}
                onMouseLeave={() => setHoveredItem(null)}
                onClick={(e) => {
                  e.stopPropagation();
                  handleItemClick(e, item);
                }}
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
                {!isDir && sortKey.startsWith('mtime') && item.modified_at && (
                  <span style={{ color: C.textDim, fontSize: 10, flexShrink: 0 }}>{formatMtimeShort(item.modified_at)}</span>
                )}
                {!isDir && <span style={{ color: C.textDim, fontSize: 10, flexShrink: 0 }}>{formatSize(item.size)}</span>}
                {isHovered && !isRenaming && selected.size <= 1 && (
                  <div style={{ display: 'flex', gap: 2, flexShrink: 0 }}>
                    <Tooltip title="重命名">
                      <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 11 }} />}
                        style={{ color: C.textDim, width: 22, height: 22, minWidth: 22 }}
                        onClick={(e) => { e.stopPropagation(); setRenaming(item.name); setRenameValue(item.name); }}
                      />
                    </Tooltip>
                    <Tooltip title={isDir ? '打包下载 (zip)' : '下载'}>
                      <Button type="text" size="small" icon={isDir ? <FileZipOutlined style={{ fontSize: 11 }} /> : <DownloadOutlined style={{ fontSize: 11 }} />}
                        style={{ color: C.textDim, width: 22, height: 22, minWidth: 22 }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (isDir) handleZipDownload([itemAbsPath]);
                          else void handleDownloadPath(itemAbsPath, item.name);
                        }}
                      />
                    </Tooltip>
                    <Tooltip title="删除">
                      <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 11 }} />}
                        style={{ color: C.danger, width: 22, height: 22, minWidth: 22 }}
                        onClick={(e) => { e.stopPropagation(); handleDeletePaths([itemAbsPath]); }}
                      />
                    </Tooltip>
                  </div>
                )}
              </div>
            );
            return (
              <Dropdown
                key={item.name}
                trigger={['contextMenu']}
                menu={{ items: buildContextMenu(item) }}
              >
                {row}
              </Dropdown>
            );
          })
        )}
      </div>

      {/* Drop Zone — kept as a click-to-upload affordance + visible cue when
          the user drags an OS file over the panel root. Internal item drags
          deliberately don't affect this hint (they use individual folders /
          breadcrumbs / parent button as drop targets). */}
      <div
        style={{
          padding: '12px 10px', borderTop: `1px solid ${C.border}`,
          background: externalDragActive ? 'rgba(108, 92, 231, 0.09)' : 'transparent',
          border: externalDragActive ? `2px dashed ${C.accent}` : `2px dashed ${C.border}`,
          borderRadius: 0, margin: '0 10px 10px',
          borderBottomLeftRadius: 'var(--jf-radius-md)', borderBottomRightRadius: 'var(--jf-radius-md)',
          textAlign: 'center', color: C.textDim, fontSize: 11, cursor: 'pointer',
          transition: 'all 0.2s',
        }}
        onClick={() => fileInputRef.current?.click()}
      >
        <UploadOutlined style={{ fontSize: 16, marginBottom: 2 }} />
        <div>拖拽上传 / 粘贴图片 (Ctrl+V)</div>
      </div>

      {/* Folder picker for 「发送到」/ 「复制到」. Disabled paths block selecting
          a destination that's the source folder or one of its subfolders. */}
      {folderPickerOpen && (
        <FolderPicker
          open={!!folderPickerOpen}
          title={folderPickerOpen.mode === 'move' ? `发送 ${folderPickerOpen.paths.length} 项到` : `复制 ${folderPickerOpen.paths.length} 项到`}
          okText={folderPickerOpen.mode === 'move' ? '移动到这里' : '复制到这里'}
          initialPath={currentPath}
          disabledPaths={folderPickerOpen.paths}
          onCancel={() => setFolderPickerOpen(null)}
          onOk={async (target) => {
            const op = folderPickerOpen.mode;
            const sources = folderPickerOpen.paths;
            setFolderPickerOpen(null);
            await performBulkMoveOrCopy(sources, target, op);
          }}
        />
      )}
    </div>
  );
}

/** Append " 副本" (or " 副本 N") before the extension to suggest a non-
 *  conflicting target name. Used by both the in-dir copy and the bulk
 *  conflict prompt so the suggested rename feels predictable. */
function suffixCopy(name: string): string {
  const dot = name.lastIndexOf('.');
  if (dot <= 0) return `${name} 副本`;
  return `${name.slice(0, dot)} 副本${name.slice(dot)}`;
}

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from 'react';
import { App } from 'antd';
import * as api from '../services/api';
import type { SplitMode } from '../components/SplitToggle';
import { getFileKind, shouldLoadText } from '../utils/fileKind';
import { pushRecentFile } from '../utils/recentFiles';

interface FileWorkspaceState {
  fileBrowserOpen: boolean;
  setFileBrowserOpen: (v: boolean | ((prev: boolean) => boolean)) => void;

  /** 文件浏览器（FilePanel）当前展示的目录路径。原本是 FilePanel 的 local state，
   *  现在提升到 context 以便从外部（比如点击聊天里的 <<FILE:>> tag）跳转到指定目录。 */
  browserPath: string;
  setBrowserPath: (p: string) => void;

  editingFile: string | null;
  editContent: string;
  editDirty: boolean;
  saving: boolean;
  openFile: (path: string) => Promise<void>;
  saveFile: () => Promise<void>;
  closeFile: (force?: boolean) => void;
  setEditContent: (s: string) => void;

  /** 在 UI 内同时「打开文件 + 跳转浏览器到所在目录 + 打开文件浏览器面板」。
   *  用于聊天里 <<FILE:/abs/path>> tag 的点击响应：一次点击让用户既能编辑/预览文件，
   *  也能在右侧文件浏览器看到它在文件系统里的位置（高亮所选文件）。
   *  anchor: 可选锚点。markdown 文件 → heading 文本/id；PDF → "page=N" / "zoom=..."。
   *  preview 组件读 pendingAnchor + consumePendingAnchor 实现 one-shot scroll. */
  revealInBrowser: (path: string, anchor?: string, isDir?: boolean) => Promise<void>;

  /** 当前待消费的深链锚点（与 editingFile 配套）。一次性：
   *  preview 组件 mount / file 切换后处理一次锚点滚动，然后调
   *  consumePendingAnchor() 清空。重复点同一个 <<FILE:>> 锚点会重新 set. */
  pendingAnchor: string | null;
  consumePendingAnchor: () => void;

  splitMode: SplitMode;
  setSplitMode: (m: SplitMode) => void;
  splitRatio: number;
  setSplitRatio: (r: number) => void;
}

const LS_SPLIT_MODE = 'jf-split-mode';
const LS_SPLIT_RATIO = 'jf-split-ratio';

function readSplitMode(): SplitMode {
  const v = localStorage.getItem(LS_SPLIT_MODE);
  if (v === 'chat' || v === 'split' || v === 'file') return v;
  return 'split';
}

function readSplitRatio(): number {
  const v = parseFloat(localStorage.getItem(LS_SPLIT_RATIO) || '');
  if (!isNaN(v) && v > 0 && v < 1) return v;
  return 0.5;
}

const Ctx = createContext<FileWorkspaceState | null>(null);

export function FileWorkspaceProvider({ children }: { children: ReactNode }) {
  const { message, modal } = App.useApp();

  const [fileBrowserOpen, setFileBrowserOpen] = useState(false);
  const [browserPath, setBrowserPath] = useState<string>('/');

  const [editingFile, setEditingFile] = useState<string | null>(null);
  const [editContent, setEditContentRaw] = useState('');
  const [editDirty, setEditDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pendingAnchor, setPendingAnchor] = useState<string | null>(null);

  const [splitMode, setSplitModeRaw] = useState<SplitMode>(readSplitMode);
  const [splitRatio, setSplitRatioRaw] = useState(readSplitRatio);

  const dirtyRef = useRef(false);
  dirtyRef.current = editDirty;

  const setSplitMode = useCallback((m: SplitMode) => {
    setSplitModeRaw(m);
    localStorage.setItem(LS_SPLIT_MODE, m);
  }, []);

  const setSplitRatio = useCallback((r: number) => {
    const clamped = Math.max(0.15, Math.min(0.85, r));
    setSplitRatioRaw(clamped);
    localStorage.setItem(LS_SPLIT_RATIO, String(clamped));
  }, []);

  const setEditContent = useCallback((s: string) => {
    setEditContentRaw(s);
    setEditDirty(true);
  }, []);

  const openFile = useCallback(async (path: string) => {
    const fileName = path.split('/').pop() || path;
    const kind = getFileKind(fileName);
    const enterSplit = () => {
      setSplitModeRaw(prev => {
        if (prev === 'chat') {
          localStorage.setItem(LS_SPLIT_MODE, 'split');
          return 'split';
        }
        return prev;
      });
    };

    // 媒体 / binary：不调 readFile，避免大文件直接拉文本
    if (!shouldLoadText(kind)) {
      setEditingFile(path);
      setEditContentRaw('');
      setEditDirty(false);
      enterSplit();
      pushRecentFile(path);
      return;
    }

    try {
      const data = await api.readFile(path);
      setEditingFile(path);
      setEditContentRaw(data.content);
      setEditDirty(false);
      enterSplit();
      pushRecentFile(path);
    } catch {
      message.error('打开文件失败');
    }
  }, [message]);

  const saveFile = useCallback(async () => {
    if (!editingFile) return;
    setSaving(true);
    try {
      await api.writeFile(editingFile, editContent);
      setEditDirty(false);
      message.success('已保存');
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  }, [editingFile, editContent, message]);

  /** 计算 path 的父目录（始终以 '/' 开头）。 */
  function parentDir(path: string): string {
    if (!path || path === '/') return '/';
    const trimmed = path.replace(/\/+$/, '');
    const idx = trimmed.lastIndexOf('/');
    if (idx <= 0) return '/';
    return trimmed.slice(0, idx);
  }

  function basename(path: string): string {
    const trimmed = path.replace(/\/+$/, '');
    return trimmed.slice(trimmed.lastIndexOf('/') + 1);
  }

  const revealInBrowser = useCallback(async (path: string, anchor?: string, isDir?: boolean) => {
    if (!path) return;
    const normalized = path.startsWith('/') ? path : '/' + path;

    // Directory detection:
    // 1. explicit flag (from @-mention chips),
    // 2. trailing slash,
    // 3. runtime metadata probe via parent directory listing for rendered chat
    //    links such as <<FILE:/docs>> where markdown has no file metadata.
    // When it's a folder, just navigate the FilePanel there — don't try to
    // open it as a file (which would call api.readFile and show an error toast).
    const looksLikeDir = isDir === true || normalized.endsWith('/');
    if (looksLikeDir) {
      const dirPath = normalized.replace(/\/+$/, '') || '/';
      setBrowserPath(dirPath);
      setFileBrowserOpen(true);
      return;
    }

    const dir = parentDir(normalized);
    const name = basename(normalized);
    try {
      const siblings = await api.listFiles(dir);
      const item = siblings.find((it) => it.name === name || it.path === normalized);
      if (item?.is_dir) {
        setBrowserPath(normalized);
        setFileBrowserOpen(true);
        setPendingAnchor(null);
        return;
      }
      // If parent metadata says this is a file, skip directory probing and open it.
    } catch {
      // Parent listing may fail for stale/inaccessible paths; fall through to openFile.
    }

    setBrowserPath(parentDir(normalized));
    setFileBrowserOpen(true);
    // Set anchor BEFORE openFile so MarkdownPreview's mount effect can pick it up
    // synchronously when content is already cached.
    setPendingAnchor(anchor || null);
    try {
      await openFile(normalized);
    } catch {
      // openFile 已经处理了 toast；这里继续保留浏览器跳转效果。
    }
  }, [openFile]);

  const consumePendingAnchor = useCallback(() => {
    setPendingAnchor(null);
  }, []);

  // 全局点击委托：聊天/文档里渲染的 [data-jf-file] 元素点击后，
  // 让 markdown 渲染层（无 React 上下文）也能触发 revealInBrowser。
  // 同时读取 [data-jf-anchor] 实现深链跳转（标题/PDF 页码）。
  useEffect(() => {
    const handler = (e: globalThis.MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (!target) return;
      const el = target.closest?.('[data-jf-file]') as HTMLElement | null;
      if (!el) return;
      const path = el.getAttribute('data-jf-file');
      if (!path) return;
      const anchor = el.getAttribute('data-jf-anchor') || undefined;
      const isDir = el.getAttribute('data-jf-is-dir') === 'true' || path.endsWith('/');
      e.preventDefault();
      e.stopPropagation();
      void revealInBrowser(path, anchor, isDir);
    };
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, [revealInBrowser]);

  const closeFile = useCallback((force = false) => {
    if (!force && dirtyRef.current) {
      modal.confirm({
        title: '未保存更改',
        content: '文件有未保存的更改，确定关闭吗？',
        okText: '关闭',
        cancelText: '取消',
        onOk: () => {
          setEditingFile(null);
          setEditDirty(false);
        },
      });
    } else {
      setEditingFile(null);
      setEditDirty(false);
    }
  }, [modal]);

  const value: FileWorkspaceState = {
    fileBrowserOpen,
    setFileBrowserOpen,
    browserPath,
    setBrowserPath,
    editingFile,
    editContent,
    editDirty,
    saving,
    openFile,
    saveFile,
    closeFile,
    setEditContent,
    revealInBrowser,
    pendingAnchor,
    consumePendingAnchor,
    splitMode,
    setSplitMode,
    splitRatio,
    setSplitRatio,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useFileWorkspace(): FileWorkspaceState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useFileWorkspace must be used inside FileWorkspaceProvider');
  return ctx;
}

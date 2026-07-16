import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  useMemo,
  type ReactNode,
} from 'react';
import { App } from 'antd';
import * as api from '../services/api';
import type { SplitMode } from '../components/SplitToggle';
import { getFileKind, shouldLoadText } from '../utils/fileKind';
import { pushRecentFile } from '../utils/recentFiles';

/** 打开中的文件 tab（按打开顺序；拖拽可重排）。仅 active 的 content 渲染到预览区。 */
export interface FileTab {
  path: string;
  content: string;
  dirty: boolean;
}

export interface FileTabSummary {
  path: string;
  dirty: boolean;
}

interface FileWorkspaceState {
  fileBrowserOpen: boolean;
  setFileBrowserOpen: (v: boolean | ((prev: boolean) => boolean)) => void;

  /** 文件浏览器（FilePanel）当前展示的目录路径。原本是 FilePanel 的 local state，
   *  现在提升到 context 以便从外部（比如点击聊天里的 <<FILE:>> tag）跳转到指定目录。 */
  browserPath: string;
  setBrowserPath: (p: string) => void;

  /** 当前激活的文件路径（兼容旧名 editingFile）。无 tab 时为 null。 */
  editingFile: string | null;
  editContent: string;
  editDirty: boolean;
  saving: boolean;
  openFile: (path: string) => Promise<void>;
  saveFile: () => Promise<void>;
  /** 关闭全部 tab（抽屉 ESC / 清空预览）。任一 dirty 则确认。 */
  closeFile: (force?: boolean) => void;
  setEditContent: (s: string) => void;

  /** 打开中的 tab 摘要（路径 + dirty），顺序即 tab 条顺序。 */
  openTabs: FileTabSummary[];
  activateTab: (path: string) => void;
  /** 关闭单个 tab；dirty 时确认。关闭后激活右邻，否则左邻。 */
  closeTab: (path: string, force?: boolean) => void;
  reorderTabs: (fromIndex: number, toIndex: number) => void;

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

const Ctx = createContext<FileWorkspaceState | null>(null);

export function FileWorkspaceProvider({ children }: { children: ReactNode }) {
  const { message, modal } = App.useApp();

  const [fileBrowserOpen, setFileBrowserOpen] = useState(false);
  const [browserPath, setBrowserPath] = useState<string>('/');

  const [tabs, setTabs] = useState<FileTab[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [pendingAnchor, setPendingAnchor] = useState<string | null>(null);

  const [splitMode, setSplitModeRaw] = useState<SplitMode>(readSplitMode);
  const [splitRatio, setSplitRatioRaw] = useState(readSplitRatio);

  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const activePathRef = useRef(activePath);
  activePathRef.current = activePath;

  const activeTab = useMemo(
    () => (activePath ? tabs.find((t) => t.path === activePath) ?? null : null),
    [tabs, activePath],
  );
  const editingFile = activePath;
  const editContent = activeTab?.content ?? '';
  const editDirty = activeTab?.dirty ?? false;

  const openTabs: FileTabSummary[] = useMemo(
    () => tabs.map(({ path, dirty }) => ({ path, dirty })),
    [tabs],
  );

  const setSplitMode = useCallback((m: SplitMode) => {
    setSplitModeRaw(m);
    localStorage.setItem(LS_SPLIT_MODE, m);
  }, []);

  const setSplitRatio = useCallback((r: number) => {
    const clamped = Math.max(0.15, Math.min(0.85, r));
    setSplitRatioRaw(clamped);
    localStorage.setItem(LS_SPLIT_RATIO, String(clamped));
  }, []);

  const enterSplit = useCallback(() => {
    setSplitModeRaw((prev) => {
      if (prev === 'chat') {
        localStorage.setItem(LS_SPLIT_MODE, 'split');
        return 'split';
      }
      return prev;
    });
  }, []);

  const setEditContent = useCallback((s: string) => {
    const path = activePathRef.current;
    if (!path) return;
    setTabs((prev) =>
      prev.map((t) => (t.path === path ? { ...t, content: s, dirty: true } : t)),
    );
  }, []);

  const activateTab = useCallback((path: string) => {
    if (!tabsRef.current.some((t) => t.path === path)) return;
    setActivePath(path);
  }, []);

  const reorderTabs = useCallback((fromIndex: number, toIndex: number) => {
    if (fromIndex === toIndex) return;
    setTabs((prev) => {
      if (
        fromIndex < 0 ||
        toIndex < 0 ||
        fromIndex >= prev.length ||
        toIndex >= prev.length
      ) {
        return prev;
      }
      const next = [...prev];
      const [item] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, item);
      return next;
    });
  }, []);

  const openFile = useCallback(async (path: string) => {
    // 已打开：只激活，保留缓存 content/dirty（不重新拉文本）
    if (tabsRef.current.some((t) => t.path === path)) {
      setActivePath(path);
      enterSplit();
      pushRecentFile(path);
      return;
    }

    const fileName = path.split('/').pop() || path;
    const kind = getFileKind(fileName);

    if (!shouldLoadText(kind)) {
      setTabs((prev) => [...prev, { path, content: '', dirty: false }]);
      setActivePath(path);
      enterSplit();
      pushRecentFile(path);
      return;
    }

    try {
      const data = await api.readFile(path);
      // 异步回来时若用户已打开同 path，只激活
      if (tabsRef.current.some((t) => t.path === path)) {
        setActivePath(path);
        enterSplit();
        pushRecentFile(path);
        return;
      }
      setTabs((prev) => [...prev, { path, content: data.content, dirty: false }]);
      setActivePath(path);
      enterSplit();
      pushRecentFile(path);
    } catch {
      message.error('打开文件失败');
    }
  }, [enterSplit, message]);

  const saveFile = useCallback(async () => {
    const path = activePathRef.current;
    const tab = tabsRef.current.find((t) => t.path === path);
    if (!path || !tab) return;
    setSaving(true);
    try {
      await api.writeFile(path, tab.content);
      setTabs((prev) =>
        prev.map((t) => (t.path === path ? { ...t, dirty: false } : t)),
      );
      message.success('已保存');
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  }, [message]);

  const closeTab = useCallback((path: string, force = false) => {
    const tab = tabsRef.current.find((t) => t.path === path);
    if (!tab) return;

    const finish = () => {
      const prev = tabsRef.current;
      const idx = prev.findIndex((t) => t.path === path);
      const next = prev.filter((t) => t.path !== path);
      setTabs(next);
      setActivePath((ap) => {
        if (ap !== path) return ap;
        if (next.length === 0) return null;
        return next[Math.min(idx, next.length - 1)].path;
      });
    };

    if (!force && tab.dirty) {
      modal.confirm({
        title: '未保存更改',
        content: `「${basename(path)}」有未保存的更改，确定关闭吗？`,
        okText: '关闭',
        cancelText: '取消',
        onOk: finish,
      });
    } else {
      finish();
    }
  }, [modal]);

  const closeFile = useCallback((force = false) => {
    const anyDirty = tabsRef.current.some((t) => t.dirty);
    const finish = () => {
      setTabs([]);
      setActivePath(null);
    };
    if (!force && anyDirty) {
      modal.confirm({
        title: '未保存更改',
        content: '有文件尚未保存，确定关闭全部标签吗？',
        okText: '关闭全部',
        cancelText: '取消',
        onOk: finish,
      });
    } else {
      finish();
    }
  }, [modal]);

  const revealInBrowser = useCallback(async (path: string, anchor?: string, isDir?: boolean) => {
    if (!path) return;
    const normalized = path.startsWith('/') ? path : '/' + path;

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
    } catch {
      // Parent listing may fail for stale/inaccessible paths; fall through to openFile.
    }

    setBrowserPath(parentDir(normalized));
    setFileBrowserOpen(true);
    setPendingAnchor(anchor || null);
    try {
      await openFile(normalized);
    } catch {
      // openFile 已经处理了 toast
    }
  }, [openFile]);

  const consumePendingAnchor = useCallback(() => {
    setPendingAnchor(null);
  }, []);

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
    openTabs,
    activateTab,
    closeTab,
    reorderTabs,
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

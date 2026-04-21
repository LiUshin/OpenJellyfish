import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from 'react';
import { App } from 'antd';
import * as api from '../services/api';
import type { SplitMode } from '../components/SplitToggle';
import { getFileKind, shouldLoadText } from '../utils/fileKind';

interface FileWorkspaceState {
  fileBrowserOpen: boolean;
  setFileBrowserOpen: (v: boolean | ((prev: boolean) => boolean)) => void;

  editingFile: string | null;
  editContent: string;
  editDirty: boolean;
  saving: boolean;
  openFile: (path: string) => Promise<void>;
  saveFile: () => Promise<void>;
  closeFile: (force?: boolean) => void;
  setEditContent: (s: string) => void;

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

  const [editingFile, setEditingFile] = useState<string | null>(null);
  const [editContent, setEditContentRaw] = useState('');
  const [editDirty, setEditDirty] = useState(false);
  const [saving, setSaving] = useState(false);

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
      return;
    }

    try {
      const data = await api.readFile(path);
      setEditingFile(path);
      setEditContentRaw(data.content);
      setEditDirty(false);
      enterSplit();
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
    editingFile,
    editContent,
    editDirty,
    saving,
    openFile,
    saveFile,
    closeFile,
    setEditContent,
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

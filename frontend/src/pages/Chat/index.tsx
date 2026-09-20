import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useOutletContext, useNavigate } from 'react-router-dom';
import { Button, Input, Select, Tooltip, App, Popover, Segmented, Alert, Popconfirm } from 'antd';
import {
  Plus,
  Trash,
  Globe,
  Palette,
  SpeakerHigh,
  VideoCamera,
  CaretDown,
  ListChecks,
  LockKey,
  MagnifyingGlass,
  FileText,
  ArrowUpRight,
  Sparkle,
  Timer,
  Stack,
} from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import * as api from '../../services/api';
import * as runtimeApi from '../../services/runtime';
import type { Conversation, Message } from '../../types';
import StreamingMessage from './components/StreamingMessage';
import ApprovalCard from './components/ApprovalCard';
import PlanTracker, { PlanCompactBar } from './components/PlanTracker';
import ImageAttachment from './components/ImageAttachment';
import type { ImageAttachmentHandle } from './components/ImageAttachment';
import ChatComposer from './components/ChatComposer';
import MessageList from './components/MessageList';
import type { MessageListHandle } from './components/MessageList';
import MentionPicker, { MAX_CANDIDATES as MENTION_MAX } from './components/MentionPicker';
import RunIndicator from './components/RunIndicator';
import QueryNavigation from './components/QueryNavigation';
import { answerPreview, conversationPreviews } from './utils/userQueryPreview';
import FileTokenInput from './components/FileTokenInput';
import type { FileTokenInputHandle } from './components/FileTokenInput';
import { useStream } from '../../stores/streamContext';
import LogoLoading from '../../components/LogoLoading';
import HeaderControls from '../../components/HeaderControls';
import { useFileWorkspace } from '../../stores/fileWorkspaceContext';
import { getYoloMode, YOLO_EVENT } from '../../utils/yoloMode';
import { getLockMode, setLockMode, getLockPaths, setLockPaths, LOCK_EVENT, type LockMode } from '../../utils/lockMode';
import WorkspaceLockPanel from './components/WorkspaceLockPanel';
import FileTreePicker, { PickerTrigger } from '../../components/FileTreePicker';
import { getLastSelectedModel, setLastSelectedModel } from '../../utils/lastSelectedModel';
import { getRecentFiles } from '../../utils/recentFiles';
import { fuzzyMatch } from '../../utils/fuzzyMatch';
import type { FileIndexEntry } from '../../services/api';
import QueryQueuePanel from './components/QueryQueuePanel';
import ChatModelSelect from '../../components/ChatModelSelect';
import RuntimeConversation from './components/RuntimeConversation';
import { newQueueItem, type QueryQueueItem } from './types/queryQueue';
import styles from './chat.module.css';

const IMG_CACHE_DB = 'jellyfish-img-cache';
const IMG_CACHE_STORE = 'images';

function openImageCacheDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IMG_CACHE_DB, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(IMG_CACHE_STORE)) {
        db.createObjectStore(IMG_CACHE_STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function cacheImages(convId: string, images: { dataUrl: string; name: string }[]) {
  try {
    const db = await openImageCacheDB();
    const tx = db.transaction(IMG_CACHE_STORE, 'readwrite');
    const store = tx.objectStore(IMG_CACHE_STORE);
    const existing: { dataUrl: string; name: string }[] = await new Promise((resolve) => {
      const r = store.get(convId);
      r.onsuccess = () => resolve(r.result || []);
      r.onerror = () => resolve([]);
    });
    store.put([...existing, ...images], convId);
  } catch { /* ignore cache errors */ }
}

async function getCachedImages(convId: string): Promise<{ dataUrl: string; name: string }[]> {
  try {
    const db = await openImageCacheDB();
    const tx = db.transaction(IMG_CACHE_STORE, 'readonly');
    const store = tx.objectStore(IMG_CACHE_STORE);
    return new Promise((resolve) => {
      const r = store.get(convId);
      r.onsuccess = () => resolve(r.result || []);
      r.onerror = () => resolve([]);
    });
  } catch { return []; }
}

// Capability bar definitions; labels resolved through `t(labelKey)` at render
// time so language flips refresh without re-creating the constant.
const CAPABILITIES = [
  { key: 'web', labelKey: 'chat.modeWeb', icon: <Globe size={16} /> },
  { key: 'image', labelKey: 'chat.modeImage', icon: <Palette size={16} /> },
  { key: 'speech', labelKey: 'chat.modeSpeech', icon: <SpeakerHigh size={16} /> },
  { key: 'video', labelKey: 'chat.modeVideo', icon: <VideoCamera size={16} /> },
];

// Task starters fill an editable draft; no model call until the user sends.
const SUGGESTION_KEYS = [
  { icon: <FileText size={20} />, key: 'document' },
  { icon: <Sparkle size={20} />, key: 'research' },
  { icon: <Timer size={20} />, key: 'routine' },
  { icon: <Stack size={20} />, key: 'service' },
];

export default function ChatPage() {
  const { t } = useTranslation();
  const { closeNavigation, sidebarSlot: siderSlot } = useOutletContext<{ closeNavigation: () => void; sidebarSlot: HTMLElement | null }>();
  const navigate = useNavigate();
  const [conversationError, setConversationError] = useState('');
  const [listError, setListError] = useState('');
  const [listLoading, setListLoading] = useState(true);
  const [conversationSearch, setConversationSearch] = useState('');
  const { message: messageApi } = App.useApp();
  const { editingFile, splitMode, setSplitMode } = useFileWorkspace();
  const stream = useStream();
  const {
    streamingConvId, isStreaming, streamBlocks, interruptData, planSteps,
    yoloApprovedConvs,
    startStream, resumeStream, stopStream, clearFinished, restoreInterrupt,
  } = stream;

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentConvId, setCurrentConvId] = useState<string | null>(null);
  const [runtimeSessionId, setRuntimeSessionId] = useState<string | null>(null);
  const [runtimeBinding, setRuntimeBinding] = useState<Conversation['runtime_binding']>(null);
  const [newChoice, setNewChoice] = useState<runtimeApi.RuntimeChoice | null>(null);
  const [runtimeProfiles, setRuntimeProfiles] = useState<runtimeApi.RuntimeProfile[]>([]);
  const [catalogReady, setCatalogReady] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [modelsError, setModelsError] = useState('');
  const [catalogVersion, setCatalogVersion] = useState(0);
  useEffect(() => {
    let disposed = false;
    const load = async () => {
      setCatalogReady(false);
      setCatalogError('');
      try {
        const caps = await runtimeApi.capabilities();
        if (caps.available) {
          const [profiles, saved] = await Promise.all([runtimeApi.profiles(), runtimeApi.preferences()]);
          if (!disposed) { setRuntimeProfiles(profiles); setNewChoice(prev => prev || (saved.runtime === 'deepagents' ? { runtime: 'deepagents' } : saved)); }
        }
      } catch (e) { if (!disposed) setCatalogError(e instanceof Error ? e.message : '模型列表加载失败'); }
      finally { if (!disposed) setCatalogReady(true); }
    };
    void load();
    return () => { disposed = true; };
  }, [catalogVersion]);
  const creatingConversation = useRef(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingConv, setLoadingConv] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [models, setModels] = useState<{ id: string; name: string; provider?: string }[]>([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [capabilities, setCapabilities] = useState<string[]>(['web']);
  const [planMode, setPlanMode] = useState(false);
  const [attachedImages, setAttachedImages] = useState<{ dataUrl: string; name: string }[]>([]);
  const [serverStreaming, setServerStreaming] = useState<string[]>([]);
  const [serverInterrupted, setServerInterrupted] = useState<string[]>([]);
  const [yoloOn, setYoloOn] = useState(getYoloMode);
  const [lockModeOn, setLockModeOn] = useState<LockMode>(getLockMode);
  const [lockPathsOn, setLockPathsOn] = useState<string[]>(getLockPaths);
  const [lockPanelOpen, setLockPanelOpen] = useState(false);
  const [lockPopoverOpen, setLockPopoverOpen] = useState(false);
  const [lockPathPickerOpen, setLockPathPickerOpen] = useState(false);
  /** Per-conversation mid-run message queue (FIFO + optional interrupt). */
  const [queryQueues, setQueryQueues] = useState<Record<string, QueryQueueItem[]>>({});
  const queryQueuesRef = useRef(queryQueues);
  queryQueuesRef.current = queryQueues;

  useEffect(() => {
    const sync = () => setYoloOn(getYoloMode());
    window.addEventListener(YOLO_EVENT, sync);
    return () => window.removeEventListener(YOLO_EVENT, sync);
  }, []);

  useEffect(() => {
    const sync = () => { setLockModeOn(getLockMode()); setLockPathsOn(getLockPaths()); };
    window.addEventListener(LOCK_EVENT, sync);
    return () => window.removeEventListener(LOCK_EVENT, sync);
  }, []);

  const imageAttachRef = useRef<ImageAttachmentHandle>(null);
  const currentConvIdRef = useRef(currentConvId);
  currentConvIdRef.current = currentConvId;
  const fileTokenInputRef = useRef<FileTokenInputHandle | null>(null);

  // ── @ 文件提及（仅 admin /chat） ──────────────────────────────────
  // 文件索引 lazy-load：第一次 @ 触发时拉一次，之后缓存到这里。
  // 整个文件树通常只有几百到几千条；前端做 in-memory fuzzy。
  const [fileIndex, setFileIndex] = useState<FileIndexEntry[]>([]);
  const fileIndexLoadedRef = useRef(false);
  const fileIndexLoadingRef = useRef(false);
  const [mention, setMention] = useState<{
    active: boolean;
    /** Cursor index of the `@` itself (so we can splice later). */
    triggerStart: number;
    query: string;
    activeIndex: number;
  }>({ active: false, triggerStart: -1, query: '', activeIndex: 0 });
  const recentPathsRef = useRef<string[]>([]);

  const ensureFileIndex = useCallback(async () => {
    if (fileIndexLoadedRef.current || fileIndexLoadingRef.current) return;
    fileIndexLoadingRef.current = true;
    try {
      const data = await api.listFileIndex('/');
      setFileIndex(data.entries);
      fileIndexLoadedRef.current = true;
    } catch { /* silent — picker will just show empty */ }
    finally { fileIndexLoadingRef.current = false; }
  }, []);

  const currentQueue = currentConvId ? (queryQueues[currentConvId] ?? []) : [];
  const isViewingStream = currentConvId === streamingConvId;
  const viewingActiveStream = isStreaming && isViewingStream;
  const hitlOnCurrent = !!interruptData && isViewingStream;
  const allowInputWhileRunning = viewingActiveStream || hitlOnCurrent;
  const showStreamBlocks = isViewingStream && (isStreaming || streamBlocks.length > 0);

  // ── 消息列表（虚拟化）相关引用 ─────────────────────────────────────
  // scrollParentEl 通过 callback ref 拿到外层 .messagesContainer DOM，
  // 用作 Virtuoso 的 customScrollParent；setState 触发 MessageList
  // 在 scrollParent 就绪后挂载。
  const [scrollParentEl, setScrollParentEl] = useState<HTMLDivElement | null>(null);
  const messageListRef = useRef<MessageListHandle>(null);
  // isAtBottom 必须是 state（不能用 ref），否则「回到底部」按钮的 className
  // 不会随用户滚动而重算更新。MessageList 内部仍用 ref 做 follow-tail 判断，
  // 不会因这个 state 频繁 re-render（只在跨阈值时变化）。
  const [isAtBottom, setIsAtBottom] = useState(true);

  // ===== 左侧 query 快速导航（悬浮在 .chatArea 左侧中部，脱离滚动容器） =====
  // 每条用户 query 一根短横，bar 数 = q 数；滚动到对应 QA 时高亮，点击跳转。
  const [activeQueryIndex, setActiveQueryIndex] = useState(-1);
  const historyMarkers = useMemo(() => conversationPreviews(messages), [messages]);
  const userMarkers = useMemo(() => {
    if (!isViewingStream || !streamBlocks.length || !historyMarkers.length) return historyMarkers;
    return [...historyMarkers.slice(0, -1), { ...historyMarkers[historyMarkers.length - 1], answer: answerPreview(streamBlocks) }];
  }, [historyMarkers, isViewingStream, streamBlocks]);
  // 滚动联动 active：取「当前视口基准线之上、最靠近基准线的那条用户消息」。
  // 直接查滚动容器内 DOM（Virtuoso 已挂载行带 data-jf-msg-*）。
  useEffect(() => {
    const el = scrollParentEl;
    if (!el) return;
    let raf = 0;
    const recompute = () => {
      raf = 0;
      const rows = el.querySelectorAll<HTMLElement>(
        '[data-jf-msg-index][data-jf-msg-role="user"]',
      );
      if (!rows.length) {
        setActiveQueryIndex((p) => (p === -1 ? p : -1));
        return;
      }
      const lineY = el.getBoundingClientRect().top + 96;
      let active = Number(rows[0].getAttribute('data-jf-msg-index'));
      rows.forEach((row) => {
        if (row.getBoundingClientRect().top <= lineY) {
          active = Number(row.getAttribute('data-jf-msg-index'));
        }
      });
      setActiveQueryIndex((p) => (p === active ? p : active));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(recompute);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    raf = requestAnimationFrame(recompute);
    return () => {
      el.removeEventListener('scroll', onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [scrollParentEl, messages]);
  const jumpToQuery = useCallback((id: string) => {
    messageListRef.current?.scrollToMessage(Number(id));
  }, []);

  // 流式追尾：优先用 MessageList 内部的 scrollFooterIntoView（messages.length>0 时），
  // messages 还为空时（新会话、首条消息流式中）MessageList 没挂，直接拉外层容器贴底。
  const scrollToBottom = useCallback(() => {
    if (messageListRef.current) {
      messageListRef.current.scrollToBottom();
    } else if (scrollParentEl) {
      scrollParentEl.scrollTop = scrollParentEl.scrollHeight;
    }
  }, [scrollParentEl]);
  const resetScroll = useCallback(() => {
    if (messageListRef.current) {
      messageListRef.current.resetScroll();
    } else if (scrollParentEl) {
      scrollParentEl.scrollTop = scrollParentEl.scrollHeight;
    }
  }, [scrollParentEl]);

  const checkServerStreaming = useCallback(async () => {
    try {
      const status = await api.getStreamingStatus();
      setServerStreaming(status.streaming);
      setServerInterrupted(status.interrupted);
    } catch { /* ignore */ }
  }, []);

  const tryRestoreInterrupt = useCallback(async (convId: string) => {
    if (isStreaming || interruptData) return;
    try {
      const state = await api.getInterruptState(convId);
      if (state.has_interrupt && state.actions) {
        restoreInterrupt(convId, { actions: state.actions, configs: state.configs });
      }
    } catch { /* ignore */ }
  }, [isStreaming, interruptData, restoreInterrupt]);

  useEffect(() => {
    loadConversations();
    loadModels();
    checkServerStreaming();
  }, []);

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        checkServerStreaming();
      }
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, [checkServerStreaming]);

  useEffect(() => {
    if (currentConvId && serverInterrupted.includes(currentConvId) && !interruptData && !isStreaming) {
      tryRestoreInterrupt(currentConvId);
    }
  }, [currentConvId, serverInterrupted, interruptData, isStreaming, tryRestoreInterrupt]);

  useEffect(() => {
    if (isViewingStream && isStreaming) {
      scrollToBottom();
    }
  }, [streamBlocks, isViewingStream, isStreaming, scrollToBottom]);

  async function loadConversations() {
    setListError('');
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (e: unknown) {
      setListError(e instanceof Error ? e.message : t('chat.loadConvFail'));
    } finally {
      setListLoading(false);
    }
  }

  async function loadModels() {
    setModelsError('');
    try {
      const data = await api.getModels();
      setModels(data.models);
      // 优先恢复用户上次手动选择的模型；不存在 / 已不可用时回退到后端默认。
      // 这样切对话、新建对话、刷新页面后选择都不丢。
      const last = getLastSelectedModel();
      const lastIsAvailable = last && data.models.some((m) => m.id === last);
      setSelectedModel(lastIsAvailable ? last : data.default);
    } catch (error) { setModelsError(error instanceof Error ? error.message : t('ux.modelsFailed')); }
  }

  // 包一层 onChange：每次手动选模型都写入 localStorage。
  const handleSelectModel = useCallback((modelId: string) => {
    setSelectedModel(modelId);
    setLastSelectedModel(modelId);
  }, []);

  const loadMessagesRef = useRef(0);
  const splitRef = useRef(splitMode);
  splitRef.current = splitMode;
  const editingRef = useRef(editingFile);
  editingRef.current = editingFile;

  const loadMessages = useCallback(async (convId: string) => {
    if (splitRef.current === 'file' && editingRef.current) setSplitMode('split');
    const seq = ++loadMessagesRef.current;
    setCurrentConvId(convId);
    setRuntimeSessionId(null);
    setRuntimeBinding(null);
    setMessages([]);
    setLoadingConv(true);
    setConversationError('');
    try {
      const detail = await api.getConversation(convId);
      if (seq !== loadMessagesRef.current) return;
      setMessages(detail.messages || []);
      setRuntimeSessionId(detail.runtime_session_id || null);
      setRuntimeBinding(detail.runtime_binding);
      if (detail.runtime_binding?.runtime === 'deepagents' && detail.runtime_binding.model) setSelectedModel(detail.runtime_binding.model);
      if (convId !== streamingConvId) {
        requestAnimationFrame(() => resetScroll());
      }
    } catch (e: unknown) {
      if (seq !== loadMessagesRef.current) return;
      setConversationError(e instanceof Error ? e.message : t('chat.loadMsgFail'));
    } finally {
      if (seq === loadMessagesRef.current) setLoadingConv(false);
    }
  }, [messageApi, resetScroll, streamingConvId, setSplitMode]);

  function handleNewChat() {
    ++loadMessagesRef.current;
    setConversationError('');
    setLoadingConv(false);
    setCurrentConvId(null);
    setRuntimeSessionId(null);
    setRuntimeBinding(null);
    setMessages([]);
    setInputValue('');
    setAttachedImages([]);
    fileTokenInputRef.current?.clear();
    setMention({ active: false, triggerStart: -1, query: '', activeIndex: 0 });
  }

  async function handleDeleteConv(convId: string) {
    if (convId === streamingConvId && isStreaming) {
      await stopStream();
    }
    try {
      await api.deleteConversation(convId);
      setConversations((prev) => prev.filter((c) => c.id !== convId));
      if (currentConvIdRef.current === convId) {
        ++loadMessagesRef.current;
        setLoadingConv(false);
        setConversationError('');
        setCurrentConvId(null);
        setRuntimeSessionId(null);
        setRuntimeBinding(null);
        setMessages([]);
      }
    } catch (e: unknown) {
      messageApi.error(e instanceof Error ? e.message : t('chat.deleteFail'));
    }
  }

  function handleStreamDone(convId: string) {
    loadConversations();
    checkServerStreaming();
    api.getConversation(convId).then((detail) => {
      if (convId === currentConvIdRef.current) {
        setMessages(detail.messages || []);
      }
      clearFinished();
      processNextQueuedMessage(convId);
    }).catch(() => {});
  }

  function removeQueueItem(convId: string, itemId?: string) {
    setQueryQueues((prev) => {
      const list = prev[convId] ?? [];
      const next = itemId ? list.filter((i) => i.id !== itemId) : list;
      return { ...prev, [convId]: next };
    });
  }

  const handleRunContinued = useCallback((convId: string, _content: string, queueId?: string) => {
    if (queueId) removeQueueItem(convId, queueId);
    api.getConversation(convId).then((detail) => {
      if (convId === currentConvIdRef.current) {
        setMessages(detail.messages || []);
      }
    }).catch(() => {});
  }, []);

  async function runInterruptItem(convId: string, item: QueryQueueItem) {
    const trimmed = item.content.trim();
    if (!trimmed) return;
    try {
      await api.stopChat(convId, {
        followUp: trimmed,
        queueId: item.id,
        keepStream: true,
      });
    } catch (e: unknown) {
      messageApi.error(e instanceof Error ? e.message : t('chat.interruptFail'));
    }
  }

  function buildStreamOpts() {
    return {
      model: selectedModel,
      capabilities,
      plan_mode: planMode || undefined,
      yolo: getYoloMode(),
      lock_mode: getLockMode(),
      lock_paths: getLockMode() === 'manual' ? getLockPaths() : undefined,
      onDone: handleStreamDone,
      onError: handleStreamError,
      onRunContinued: handleRunContinued,
      onWorkspaceLock: (mode: string, granted: string[], conflicts?: { path: string; holder: string }[]) => {
        if (conflicts && conflicts.length > 0) {
          messageApi.warning(
            `部分区域被占用（${conflicts.map((c) => c.path).join('、')}），本轮以只读运行对应区域`,
          );
        } else if (mode !== 'agent' && granted.length === 0) {
          messageApi.warning('工作区当前被其它进程占满，本轮为只读');
        }
      },
    };
  }

  function processNextQueuedMessage(convId: string) {
    const list = queryQueuesRef.current[convId] ?? [];
    const next = list.find((i) => i.content.trim());
    if (!next) return;
    removeQueueItem(convId, next.id);
    const userMessage: Message = { role: 'user', content: next.content.trim() };
    if (convId === currentConvIdRef.current) {
      setMessages((prev) => [...prev, userMessage]);
    }
    startStream(convId, next.content.trim(), buildStreamOpts());
  }

  function handleQueueChange(items: QueryQueueItem[]) {
    if (!currentConvId) return;
    setQueryQueues((q) => ({ ...q, [currentConvId]: items }));
  }

  function handleStreamError(convId: string, _msg: string) {
    setTimeout(() => {
      api.getConversation(convId).then((detail) => {
        if (convId === currentConvIdRef.current) {
          setMessages(detail.messages || []);
        }
        clearFinished();
        loadConversations();
        checkServerStreaming();
      }).catch(() => {});
    }, 600);
  }

  function handleResume(decisions: unknown[]) {
    if (!currentConvId || !interruptData) return;
    resumeStream(currentConvId, decisions, buildStreamOpts());
  }

  async function handleStop() {
    if (!isStreaming) return;
    await stopStream();
    resetScroll();

    const cid = streamingConvId;
    if (cid) {
      setTimeout(async () => {
        try {
          const detail = await api.getConversation(cid);
          if (cid === currentConvIdRef.current) {
            setMessages(detail.messages || []);
          }
          clearFinished();
          loadConversations();
          checkServerStreaming();
        } catch { /* ignore */ }
      }, 500);
    }
  }

  async function handleForceStop(convId: string) {
    try {
      await api.stopChat(convId);
      messageApi.success(t('chat.abortPrevSuccess'));
      setTimeout(async () => {
        await checkServerStreaming();
        const detail = await api.getConversation(convId);
        if (convId === currentConvIdRef.current) {
          setMessages(detail.messages || []);
        }
        loadConversations();
      }, 1000);
    } catch {
      messageApi.error(t('chat.abortPrevFail'));
    }
  }

  function navigateToStreamingConv() {
    if (streamingConvId) loadMessages(streamingConvId);
  }

  // ── @ 文件提及 helpers ──────────────────────────────────────────
  // 检测光标前是否有「行首/空白后跟着的 @」，并解析 @ 之后到光标之间的 query。
  // 返回 null 表示当前光标不在 @ 上下文里。
  function detectMentionTrigger(value: string, cursor: number): { triggerStart: number; query: string } | null {
    if (cursor <= 0) return null;
    // 从光标向前扫，直到遇到空白/换行/字符串首
    let i = cursor - 1;
    while (i >= 0) {
      const ch = value[i];
      if (ch === '@') {
        // 必须是行首或前面是空白
        const before = i === 0 ? '' : value[i - 1];
        if (i === 0 || before === ' ' || before === '\n' || before === '\t') {
          const query = value.slice(i + 1, cursor);
          // query 不能含空格/换行（一旦输入空格/换行就关闭 picker）
          if (/[\s\n\r]/.test(query)) return null;
          return { triggerStart: i, query };
        }
        return null;
      }
      // 任何空白字符都视为词边界 → 没有有效 @ trigger
      if (ch === ' ' || ch === '\n' || ch === '\t') return null;
      i--;
    }
    return null;
  }

  function handleInputChange(value: string) {
    setInputValue(value);
  }

  /** Called by FileTokenInput's internal mention detector on every keystroke. */
  function handleMentionTrigger(trig: { triggerStart: number; query: string } | null) {
    if (trig) {
      if (!mention.active) {
        recentPathsRef.current = getRecentFiles();
        ensureFileIndex();
      }
      setMention({
        active: true,
        triggerStart: trig.triggerStart,
        query: trig.query,
        activeIndex: 0,
      });
    } else if (mention.active) {
      setMention((m) => ({ ...m, active: false }));
    }
  }

  function insertMention(item: FileIndexEntry) {
    if (!mention.active) return;
    const before = inputValue.slice(0, mention.triggerStart);
    const queryEnd = mention.triggerStart + 1 + mention.query.length;
    const after = inputValue.slice(queryEnd);
    // Chip token — no trailing space; the chip itself acts as an atom and
    // the user can continue typing right after it.
    const token = `[[FILE:${item.path}]]`;
    const next = before + token + after;
    setInputValue(next);
    setMention({ active: false, triggerStart: -1, query: '', activeIndex: 0 });
    // After FileTokenInput re-hydrates the DOM from the new value, move the
    // caret to just after the inserted chip.
    requestAnimationFrame(() => {
      const fti = fileTokenInputRef.current;
      if (fti) {
        fti.focus();
        fti.setCaretPosition(before.length + token.length);
      }
    });
  }

  async function handleSend(text?: string) {
    if (loadingConv || runtimeSessionId || conversationError) return;
    const msg = text ?? inputValue.trim();
    const hasImages = attachedImages.length > 0;
    if (!msg && !hasImages) return;

    const viewingActiveStream = isStreaming && streamingConvId === currentConvId;
    const hitlOnCurrent = !!interruptData && streamingConvId === currentConvId;

    // Mid-run: enqueue instead of blocking (Codex-style).
    if (viewingActiveStream || hitlOnCurrent) {
      if (hasImages) {
        messageApi.warning(t('chat.queueNoImages'));
        return;
      }
      const convId = currentConvId ?? streamingConvId;
      if (!convId) return;

      // Always enqueue; per-item interrupt is triggered from the queue panel.
      const item = newQueueItem(msg, 'queue');
      setQueryQueues((prev) => ({
        ...prev,
        [convId]: [...(prev[convId] ?? []), item],
      }));
      setInputValue('');
      fileTokenInputRef.current?.clear();
      setMention({ active: false, triggerStart: -1, query: '', activeIndex: 0 });
      return;
    }

    if (isStreaming || interruptData) {
      const streamTitle = conversations.find((c) => c.id === streamingConvId)?.title || t('chat.conversationFallback');
      messageApi.warning({
        content: t('chat.queueRunning', { title: streamTitle }),
        duration: 3,
      });
      return;
    }
    if (serverStreaming.includes(currentConvId ?? '')) {
      messageApi.warning({
        content: t('chat.queueRunningPrev'),
        duration: 3,
      });
      return;
    }
    if (serverInterrupted.includes(currentConvId ?? '')) {
      messageApi.warning({
        content: t('chat.queueApprovalPending'),
        duration: 3,
      });
      return;
    }

    let convId = currentConvId;
    if (!convId) {
      if (creatingConversation.current) return;
      creatingConversation.current = true;
      const creationSeq = loadMessagesRef.current;
      try {
        if (!catalogReady || catalogError) {
          messageApi.error(catalogError || '正在加载模型，请稍后发送');
          return;
        }
        const choice: runtimeApi.RuntimeChoice = newChoice?.runtime && newChoice.runtime !== 'deepagents'
          ? newChoice : { runtime: 'deepagents', model: selectedModel || undefined };
        const title = msg ? msg.slice(0, 30) : t('chat.imgConvTitle');
        const conv = await api.createConversation(title, choice);
        setConversations((prev) => [conv, ...prev]);
        convId = conv.id;
        if (conv.runtime_session_id) {
          try {
            await runtimeApi.turn(conv.id, crypto.randomUUID(), msg, choice.model, attachedImages.map(f => ({ name: f.name, data_url: f.dataUrl })), getYoloMode());
            if (creationSeq === loadMessagesRef.current) {
              setInputValue('');
              setAttachedImages([]);
              fileTokenInputRef.current?.clear();
            }
          } finally {
            if (creationSeq === loadMessagesRef.current) {
              setCurrentConvId(conv.id);
              setRuntimeSessionId(conv.runtime_session_id);
              setRuntimeBinding(conv.runtime_binding);
            }
          }
          return;
        }
        setCurrentConvId(convId);
        setRuntimeSessionId(conv.runtime_session_id || null);
        setRuntimeBinding(conv.runtime_binding);
      } catch (e) {
        messageApi.error(e instanceof Error ? e.message : t('chat.createConvFail'));
        return;
      } finally {
        creatingConversation.current = false;
      }
    }

    const displayContent = hasImages
      ? (msg || '') + attachedImages.map((img) => `\n![${img.name}](${img.dataUrl})`).join('')
      : msg;
    const userMessage: Message = { role: 'user', content: displayContent };
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    fileTokenInputRef.current?.clear();
    setMention({ active: false, triggerStart: -1, query: '', activeIndex: 0 });
    resetScroll();

    let messageContent: string | unknown[] = msg;
    if (hasImages) {
      const parts: unknown[] = [];
      if (msg) parts.push({ type: 'text', text: msg });
      parts.push(
        ...attachedImages.map((img) => ({
          type: 'image_url',
          image_url: { url: img.dataUrl },
        })),
      );
      messageContent = parts;
      cacheImages(convId, attachedImages);
      setAttachedImages([]);
    }

    startStream(convId, messageContent, buildStreamOpts());
  }

  // Keyboard callbacks forwarded from FileTokenInput's internal keydown handler.
  // The FileTokenInput fires these when mentionPickerActive=true.
  function handleMentionNavDown() {
    const candidates = fuzzyMatch(fileIndex, mention.query, recentPathsRef.current, MENTION_MAX);
    if (candidates.length > 0) {
      setMention((m) => ({ ...m, activeIndex: (m.activeIndex + 1) % candidates.length }));
    }
  }
  function handleMentionNavUp() {
    const candidates = fuzzyMatch(fileIndex, mention.query, recentPathsRef.current, MENTION_MAX);
    if (candidates.length > 0) {
      setMention((m) => ({ ...m, activeIndex: (m.activeIndex - 1 + candidates.length) % candidates.length }));
    }
  }
  function handleMentionConfirm() {
    const candidates = fuzzyMatch(fileIndex, mention.query, recentPathsRef.current, MENTION_MAX);
    if (candidates.length > 0) {
      insertMention(candidates[mention.activeIndex]?.item ?? candidates[0].item);
    }
  }
  function handleMentionDismiss() {
    setMention((m) => ({ ...m, active: false }));
  }

  /** Called by FileTokenInput when user pastes image files. */
  function handleImagePaste(imageFiles: File[]) {
    if (isStreaming) return;
    void imageAttachRef.current?.addFiles(imageFiles);
  }

  const currentTitle = currentConvId
    ? conversations.find((c) => c.id === currentConvId)?.title || t('chat.conversationFallback')
    : t('ux.workspaceTitle');


  const searchTerm = conversationSearch.trim().toLocaleLowerCase();
  const visibleConversations = conversations.filter(conv => !searchTerm || conv.title.toLocaleLowerCase().includes(searchTerm));

  const sidebarContent = (
    <div className={styles.chatContainer} style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent' }}>
      <div className={styles.sidebarHeader}>
        <Button
          className={styles.newChatBtn}
          icon={<Plus size={16} />}
          onClick={() => { setConversationSearch(''); handleNewChat(); closeNavigation(); }}
        >
          {t('chat.newChat')}
        </Button>
      </div>
      <div className={styles.sidebarSearch}>
        <Input aria-label={t('chat.searchConversations')} placeholder={t('chat.searchConversations')}
          prefix={<MagnifyingGlass size={15} />} allowClear value={conversationSearch}
          onChange={e => setConversationSearch(e.target.value)}
          onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); setConversationSearch(''); } }} />
      </div>
      <div className={styles.convListLabel}><span>{t('chat.conversationList')}</span><span>{visibleConversations.length}</span></div>
      <nav aria-label={t('chat.conversationList')} className={styles.convList} aria-busy={listLoading}>
        {listError && <Alert type="error" message={t('chat.loadConvFail')} description={listError}
          action={<Button size="small" onClick={() => void loadConversations()}>{t('ux.retry')}</Button>} />}
        {listLoading && <div role="status" className={styles.convEmpty}>{t('ux.loading')}</div>}
        {visibleConversations.map((conv) => {
          const isConvStreaming = conv.id === streamingConvId && isStreaming;
          const isConvHitl = conv.id === streamingConvId && !!interruptData && !isStreaming;
          return (
            <div
              key={conv.id}
              className={`${styles.convItem} ${conv.id === currentConvId ? styles.active : ''}`}
            >
              <button type="button" className={styles.convSelect} title={conv.title}
                aria-current={conv.id === currentConvId ? 'page' : undefined}
                onClick={() => { void loadMessages(conv.id); closeNavigation(); }}>
              {isConvStreaming && (
                <span title={t('chat.streamingTitle')} style={{ display: 'inline-flex' }}>
                  <RunIndicator state="running" label={t('chat.streamingTitle')} />
                </span>
              )}
              {isConvHitl && (
                <span title={t('chat.hitlBadge')} style={{ display: 'inline-flex' }}>
                  <RunIndicator state="approval" label={t('chat.hitlBadge')} />
                </span>
              )}
              <span className={styles.convTitle}>{conv.title}</span>
              </button>
              <Popconfirm title={t('ux.deleteTitle')} description={t('ux.deleteDescription')}
                okText={t('ux.delete')} cancelText={t('common.cancel')} okButtonProps={{ danger: true }}
                onConfirm={() => handleDeleteConv(conv.id)}>
              <Button
                type="text"
                size="small"
                danger
                icon={<Trash size={14} />}
                className={styles.convDelete}
                aria-label={t('chat.deleteConversation', { title: conv.title })}
                title={t('chat.deleteConversation', { title: conv.title })}
                onClick={(e) => {
                  e.stopPropagation();
                }}
              />
              </Popconfirm>
            </div>
          );
        })}
        {!listLoading && !listError && visibleConversations.length === 0 && (
          <div className={styles.convEmpty} role="status">
            {searchTerm ? t('chat.noMatchingConversations') : t('chat.emptyConversations')}
            {searchTerm && <Button type="link" size="small" onClick={() => setConversationSearch('')}>{t('chat.clearSearch')}</Button>}
          </div>
        )}
      </nav>
    </div>
  );

  return (
    <div className={styles.chatContainer}>
      {siderSlot && createPortal(sidebarContent, siderSlot)}

      {/* ===== Chat Area ===== */}
      <div className={styles.chatArea}>
        {/* 左侧 query 快速导航：悬浮在 chatArea 左侧垂直居中，脱离滚动容器，
            不随消息滚动消失；bar 数 = q 数，active 高亮，点击跳转。 */}
        {!runtimeSessionId && userMarkers.length > 0 && (
          <QueryNavigation items={userMarkers} activeId={String(activeQueryIndex)} onJump={jumpToQuery} />
        )}
        {/* Header */}
        <div className={styles.chatHeader}>
          <span className={styles.chatTitle}>{currentTitle}</span>
          <Tooltip title="活跃进程 / 工作区锁">
            <button
              className={styles.capBtn}
              style={{ marginLeft: 8 }}
              aria-label="活跃进程 / 工作区锁"
              onClick={() => setLockPanelOpen(true)}
            >
              <LockKey size={16} />
            </button>
          </Tooltip>
          {(!editingFile || splitMode === 'chat') && <HeaderControls />}
        </div>

        {(catalogError || modelsError) && <Alert type="warning" showIcon message={t('ux.modelsFailed')}
          description={catalogError || modelsError} action={<Button size="small" onClick={() => { setCatalogVersion(value => value + 1); void loadModels(); }}>{t('ux.retry')}</Button>} />}
        {/* Messages */}
        {runtimeSessionId && currentConvId ? <RuntimeConversation key={runtimeSessionId} sid={runtimeSessionId} conversationId={currentConvId} history={messages} profiles={runtimeProfiles} onChanged={loadConversations} /> : <>
        <div className={styles.messagesContainer} ref={setScrollParentEl}>
          {conversationError ? (
            <div className={styles.recoveryState}><Alert type="error" showIcon
              message={t('chat.loadMsgFail')} description={conversationError} />
              <Button type="primary" onClick={() => currentConvId && void loadMessages(currentConvId)}>{t('ux.retry')}</Button>
            </div>
          ) : loadingConv ? (
            <LogoLoading size={240} />
          ) : messages.length === 0 && !showStreamBlocks ? (
            <section className={styles.emptyState} aria-labelledby="welcome-heading">
              <div className={styles.welcomeBrand}><img src="/media_resources/jellyfishlogo.png" alt="" width="64" height="64" /><span>YOUR JELLYFISH WORKSPACE</span></div>
              <h1 id="welcome-heading" className={styles.welcomeHeading}>{t('ux.welcomeTitle')}</h1>
              <p className={styles.welcomeDescription}>{t('ux.welcomeDescription')}</p>
              <div className={styles.starterGrid}>
                {SUGGESTION_KEYS.map(s => <button type="button" key={s.key} className={styles.starterCard}
                  onClick={() => { setInputValue(t(`ux.starters.${s.key}.prompt`)); fileTokenInputRef.current?.focus(); }}>
                  <span className={styles.starterIcon}>{s.icon}</span><ArrowUpRight size={16} className={styles.starterArrow} />
                  <strong>{t(`ux.starters.${s.key}.title`)}</strong><span>{t(`ux.starters.${s.key}.description`)}</span>
                </button>)}
              </div>
              <div className={styles.welcomeFootnote}><span>{t('ux.starterHint')}</span><button type="button" onClick={() => navigate('/settings/general')}>{t('ux.configureEngine')} <ArrowUpRight size={13} /></button></div>
            </section>
          ) : (
            <>
              {messages.length > 0 && (
                <MessageList
                  ref={messageListRef}
                  messages={messages}
                  conversationId={currentConvId}
                  scrollParent={scrollParentEl}
                  followStream={isViewingStream && isStreaming}
                  onAtBottomChange={setIsAtBottom}
                />
              )}
              {/*
                ⚠️ 不要把这些「实时」节点塞进 Virtuoso 的 Footer。
                react-virtuoso v4 的 Footer 走 useEmitterValue/useSyncExternalStore，
                高频 context 推送（流式 args_delta 每秒几十次 setStreamBlocks）会被
                内部 batching 吞掉，导致 write_file/edit_file 打字机停在「等待内容…」
                直到流结束才一次性刷新。把它们作为 MessageList 的兄弟节点直接挂到
                messagesContainer 下，所有 setState 都直接触发 React 重渲染，无中间层。
                由于使用 customScrollParent，scrollHeight 仍然包含这些节点，
                scrollFooterIntoView 行为完全不变。
              */}
              {showStreamBlocks && (
                <StreamingMessage blocks={streamBlocks} isStreaming={isStreaming} status={interruptData ? 'waiting_approval' : isStreaming ? 'running' : 'completed'} />
              )}
              {isViewingStream && planSteps.length > 0 && (
                <PlanTracker steps={planSteps} />
              )}
              {isViewingStream && interruptData && currentConvId && (
                <ApprovalCard
                  actions={interruptData.actions as never[]}
                  configs={(interruptData.configs ?? []) as never[]}
                  conversationId={currentConvId}
                  onResume={handleResume}
                />
              )}
            </>
          )}

          {/* Scroll to bottom button — 长会话上滑后随时可一键回到底部，
              不再限定流式状态（旧版只在 streaming 时显示，UX 偏弱）。*/}
          <button
            className={`${styles.scrollBottomBtn} ${
              !isAtBottom && messages.length > 0 ? styles.visible : ''
            }`}
            onClick={resetScroll}
          >
            <CaretDown size={14} /> {t('chat.backToBottom')}
          </button>
        </div>

        {/* Streaming-elsewhere banner (frontend-connected stream on another conv) */}
        {(isStreaming || interruptData) && !isViewingStream && (
          <div className={styles.streamElsewhereBanner}>
            <span className={styles.streamElsewhereText}>
              <RunIndicator
                state={interruptData ? 'approval' : 'running'}
                label={interruptData ? t('chat.runStateAwaiting') : t('chat.runStateRunning')}
              />
              「{conversations.find((c) => c.id === streamingConvId)?.title || t('chat.conversationFallback')}」
              {interruptData ? t('chat.runStateAwaiting') : t('chat.runStateRunning')}
            </span>
            <div className={styles.streamElsewhereActions}>
              <Button size="small" type="link" onClick={navigateToStreamingConv}>
                {t('chat.viewBtn')}
              </Button>
              {isStreaming && (
                <Button size="small" type="link" danger onClick={handleStop}>
                  {t('chat.stopBtn')}
                </Button>
              )}
            </div>
          </div>
        )}

        {/* Server-streaming banner (backend still streaming after page refresh / disconnect) */}
        {!isStreaming && !interruptData && currentConvId && serverStreaming.includes(currentConvId) && (
          <div className={styles.streamElsewhereBanner}>
            <span className={styles.streamElsewhereText}>
              {t('chat.prevRoundRunning')}
            </span>
            <div className={styles.streamElsewhereActions}>
              <Button size="small" type="link" danger onClick={() => handleForceStop(currentConvId)}>
                {t('chat.terminateAndSave')}
              </Button>
              <Button
                size="small"
                type="link"
                onClick={async () => {
                  await checkServerStreaming();
                  if (currentConvId) loadMessages(currentConvId);
                }}
              >
                {t('chat.refreshState')}
              </Button>
            </div>
          </div>
        )}

        {/* Server-interrupted banner (HITL pending after page refresh) */}
        {!isStreaming && !interruptData && currentConvId && serverInterrupted.includes(currentConvId) && (
          <div className={styles.streamElsewhereBanner}>
            <span className={styles.streamElsewhereText}>
              {t('chat.pendingApproval')}
            </span>
            <div className={styles.streamElsewhereActions}>
              <Button size="small" type="link" onClick={() => tryRestoreInterrupt(currentConvId)}>
                {t('chat.resumeApproval')}
              </Button>
              <Button size="small" type="link" danger onClick={() => handleForceStop(currentConvId)}>
                {t('chat.terminateAndSave')}
              </Button>
            </div>
          </div>
        )}

        {/* Plan compact bar */}
        {isViewingStream && planSteps.length > 0 && isStreaming && (
          <PlanCompactBar steps={planSteps} onClick={resetScroll} />
        )}

        {/* Input */}
        <div className={styles.inputArea}>
          <QueryQueuePanel
            items={currentQueue}
            onChange={handleQueueChange}
            onRemove={(id) => currentConvId && removeQueueItem(currentConvId, id)}
            onRunInterrupt={(item) => currentConvId && runInterruptItem(currentConvId, item)}
            canInterrupt={isStreaming && isViewingStream && !interruptData}
            hitlLocked={hitlOnCurrent}
          />
          <ChatComposer
            attachments={<ImageAttachment
              ref={imageAttachRef}
              allowFiles={!currentConvId && !!newChoice && newChoice.runtime !== 'deepagents'}
              images={attachedImages}
              onImagesChange={setAttachedImages}
              disabled={isStreaming && !allowInputWhileRunning}
            />}
            tools={!currentConvId && newChoice && newChoice.runtime !== 'deepagents' ? undefined : <><div className={styles.inputToolbarDivider} />{CAPABILITIES.map((cap) => (
              <Tooltip key={cap.key} title={t(cap.labelKey)}>
                <button
                  className={`${styles.capBtn} ${capabilities.includes(cap.key) ? styles.capBtnActive : ''}`}
                  aria-label={t(cap.labelKey)}
                  aria-pressed={capabilities.includes(cap.key)}
                  onClick={() => {
                    setCapabilities((prev) =>
                      prev.includes(cap.key)
                        ? prev.filter((c) => c !== cap.key)
                        : [...prev, cap.key],
                    );
                  }}
                >
                  {cap.icon}
                </button>
              </Tooltip>
            ))}
            <div className={styles.inputToolbarDivider} />
            <Tooltip title={planMode ? t('chat.planModeOn') : t('chat.planModeHint')}>
              <button
                className={`${styles.capBtn} ${planMode ? styles.capBtnActive : ''}`}
                aria-label={t('chat.planModeHint')}
                aria-pressed={planMode}
                onClick={() => setPlanMode(!planMode)}
              >
                <ListChecks size={16} />
              </button>
            </Tooltip>
            <div className={styles.inputToolbarDivider} />
            <Popover
              open={lockPopoverOpen}
              onOpenChange={setLockPopoverOpen}
              trigger="click"
              placement="top"
              content={
                <div style={{ width: 260, display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ fontSize: 12, color: 'var(--jf-text-dim)' }}>
                    本轮对话的工作区写锁策略：
                  </div>
                  <Segmented
                    size="small"
                    block
                    value={lockModeOn}
                    onChange={(v) => setLockMode(v as LockMode)}
                    options={[
                      { label: '自动', value: 'auto' },
                      { label: '手动', value: 'manual' },
                      { label: 'Agent 自选', value: 'agent' },
                    ]}
                  />
                  <div style={{ fontSize: 11, color: 'var(--jf-text-dim)', lineHeight: 1.5 }}>
                    {lockModeOn === 'auto' && '默认：抢占当前空闲的最大区域，独占会话可写全部，并发会话自动避让。'}
                    {lockModeOn === 'manual' && '锁定你指定的目录或文件（可多选），其它区域只读。'}
                    {lockModeOn === 'agent' && '不预先锁定，Agent 需要写入时自行调用工具声明区域。'}
                  </div>
                  {lockModeOn === 'manual' && (
                    <PickerTrigger
                      value={lockPathsOn}
                      placeholder="点击选择要锁定的路径…"
                      onClick={() => setLockPathPickerOpen(true)}
                    />
                  )}
                  <Button size="small" type="link" style={{ padding: 0, textAlign: 'left' }} onClick={() => { setLockPopoverOpen(false); setLockPanelOpen(true); }}>
                    查看活跃进程 / 已锁区域 →
                  </Button>
                </div>
              }
            >
              <Tooltip title={`工作区锁：${lockModeOn === 'auto' ? '自动' : lockModeOn === 'manual' ? '手动' : 'Agent 自选'}`}>
                <button aria-label="工作区锁策略" className={`${styles.capBtn} ${lockModeOn !== 'auto' ? styles.capBtnActive : ''}`}>
                  <LockKey size={16} />
                </button>
              </Tooltip>
            </Popover></>}
            model={<ChatModelSelect
              value={!currentConvId && newChoice?.runtime !== 'deepagents' && newChoice ? newChoice : { runtime: 'deepagents', model: selectedModel }}
              onChange={choice => { setNewChoice(choice); if (choice.runtime === 'deepagents' && choice.model) handleSelectModel(choice.model); }}
              models={models} profiles={runtimeProfiles} bound={!!currentConvId}
              loading={!catalogReady} disabled={!catalogReady || loadingConv}
            />}
            input={<><FileTokenInput
              ref={fileTokenInputRef}
              value={inputValue}
              onChange={handleInputChange}
              onSend={() => handleSend()}
              onMentionTrigger={handleMentionTrigger}
              mentionPickerActive={mention.active}
              onMentionNavDown={handleMentionNavDown}
              onMentionNavUp={handleMentionNavUp}
              onMentionConfirm={handleMentionConfirm}
              onMentionDismiss={handleMentionDismiss}
              placeholder={
                allowInputWhileRunning
                  ? t('chat.inputPlaceholderQueue')
                  : t('chat.composePlaceholder')
              }
              disabled={isStreaming && !allowInputWhileRunning}
              onImagePaste={handleImagePaste}
            />
            {mention.active && (
              <MentionPicker
                visible
                query={mention.query}
                items={fileIndex}
                recentPaths={recentPathsRef.current}
                activeIndex={mention.activeIndex}
                onActiveIndexChange={(idx) => setMention((m) => ({ ...m, activeIndex: idx }))}
                onSelect={insertMention}
              />
            )}</>}
            onUpload={() => imageAttachRef.current?.triggerUpload()}
            uploadDisabled={isStreaming || attachedImages.length >= 5}
            hasAttachments={attachedImages.length > 0}
            onTranscript={text => void handleSend(text)} voiceDisabled={isStreaming && !allowInputWhileRunning}
            onStop={viewingActiveStream ? () => void handleStop() : undefined}
            onSend={() => void handleSend()}
            sendDisabled={loadingConv || !!conversationError || (!inputValue.trim() && attachedImages.length === 0)
              || (isStreaming && !allowInputWhileRunning) || (!!interruptData && !hitlOnCurrent)
              || serverStreaming.includes(currentConvId ?? '')
              || (serverInterrupted.includes(currentConvId ?? '') && !hitlOnCurrent)}
          />
          {yoloOn && currentConvId && yoloApprovedConvs.has(currentConvId) && (
            <div
              className={styles.yoloFooterTag}
              title={t('chat.yoloAutoApprove')}
            >
              <span className={styles.yoloFooterDot} />
              yolo
            </div>
          )}
        </div>
        </>}
      </div>
      <WorkspaceLockPanel open={lockPanelOpen} onClose={() => setLockPanelOpen(false)} />
      <FileTreePicker
        open={lockPathPickerOpen}
        title="选择要锁定的工作区路径"
        rootPath="/"
        value={lockPathsOn}
        pathOutput="absolute"
        allToken="/"
        enableAllShortcut
        allShortcutTitle="锁定全部工作区 (/)"
        allShortcutHint="打开后将锁定整个工作区写权限，忽略下方勾选"
        emptyHint="未选 = 手动模式下本轮不预先锁定任何区域（只读）"
        onCancel={() => setLockPathPickerOpen(false)}
        onOk={(next) => {
          setLockPaths(next);
          setLockPathsOn(next);
          setLockPathPickerOpen(false);
        }}
      />
    </div>
  );
}

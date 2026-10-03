import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useOutletContext, useNavigate, useLocation } from 'react-router-dom';
import { Button, Input, Select, Tooltip, App, Popover, Segmented, Alert, Popconfirm, Dropdown, Modal } from 'antd';
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
  Folder,
  DotsThree,
  Flask,
  GitBranch,
} from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import * as api from '../../services/api';
import * as runtimeApi from '../../services/runtime';
import type { Conversation, Message, Project, ServiceConfig } from '../../types';
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
import { useStream, type StreamIdentity } from '../../stores/streamContext';
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
import RuntimeConversation, { type RuntimeInitialSubmission } from './components/RuntimeConversation';
import TracingView from './components/TracingView';
import ChatWelcome from './components/ChatWelcome';
import ProjectOverview from './components/ProjectOverview';
import { newQueueItem, type QueryQueueItem } from './types/queryQueue';
import { readRuntimeQueues, runtimeQueueStorageKey, writeRuntimeQueues, type RuntimeQueuedTurn, type RuntimeQueues } from './types/runtimeQueue';
import { readRuntimePending, runtimePendingStorageKey, writeRuntimePending, type RuntimePendingTurn, type RuntimePendingTurns } from './types/runtimePending';
import styles from './chat.module.css';

const IMG_CACHE_DB = 'jellyfish-img-cache';
const IMG_CACHE_STORE = 'images';
type DeferredDeepagentSend = {
  text: string;
  images: { dataUrl: string; name: string }[];
  previousUserCount: number;
};
type RuntimeTraceSnapshot = {
  sid: string;
  runs: runtimeApi.RuntimeRun[];
  loading: boolean;
  error: string;
};

async function readRuntimeSession(sid: string): Promise<runtimeApi.RuntimeSession> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 8000);
  try { return await runtimeApi.session(sid, controller.signal); }
  finally { window.clearTimeout(timeout); }
}

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

export default function ChatPage() {
  const { t } = useTranslation();
  const { closeNavigation, sidebarSlot: siderSlot } = useOutletContext<{ closeNavigation: () => void; sidebarSlot: HTMLElement | null }>();
  const navigate = useNavigate();
  const location = useLocation();
  const routeQuery = new URLSearchParams(location.search);
  const selectedProjectId = routeQuery.get('project');
  const routeConversationId = routeQuery.get('conversation');
  const isProjectOverview = !!selectedProjectId && !routeConversationId && routeQuery.get('new') !== '1';
  const [conversationError, setConversationError] = useState('');
  const [listError, setListError] = useState('');
  const [listLoading, setListLoading] = useState(true);
  const [conversationSearch, setConversationSearch] = useState('');
  const { message: messageApi } = App.useApp();
  const { editingFile, splitMode, setSplitMode, fileBrowserOpen, revealInBrowser } = useFileWorkspace();
  const stream = useStream();
  const {
    streamingConvId, isStreaming, stopState, stopError, streamBlocks, interruptData, planSteps,
    yoloApprovedConvs,
    startStream, resumeStream, stopStream, clearFinished, isCurrentStream, getStreamGeneration, restoreInterrupt,
  } = stream;

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectListError, setProjectListError] = useState('');
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [creatingProject, setCreatingProject] = useState(false);
  const [currentConvId, setCurrentConvId] = useState<string | null>(null);
  const [runtimeSessionId, setRuntimeSessionId] = useState<string | null>(null);
  const [runtimeBinding, setRuntimeBinding] = useState<Conversation['runtime_binding']>(null);
  const [testServiceId, setTestServiceId] = useState<string | null>(null);
  const [testPickerOpen, setTestPickerOpen] = useState(false);
  const [selectedTestServiceId, setSelectedTestServiceId] = useState<string | null>(null);
  const [testModeUpdating, setTestModeUpdating] = useState(false);
  const testModeUpdatingRef = useRef(false);
  const [testServices, setTestServices] = useState<ServiceConfig[]>([]);
  const [testServicesLoaded, setTestServicesLoaded] = useState(false);
  const [testServicesLoading, setTestServicesLoading] = useState(false);
  const [testServicesError, setTestServicesError] = useState('');
  const testServicesLoadingRef = useRef(false);
  const loadTestServices = useCallback(async () => {
    if (testServicesLoadingRef.current) return;
    testServicesLoadingRef.current = true;
    setTestServicesLoading(true);
    setTestServicesError('');
    try {
      setTestServices(await api.listServices() as ServiceConfig[]);
      setTestServicesLoaded(true);
    } catch (error) {
      setTestServicesError(error instanceof Error ? error.message : 'Service 列表加载失败');
    } finally {
      testServicesLoadingRef.current = false;
      setTestServicesLoading(false);
    }
  }, []);
  useEffect(() => {
    if (testServiceId && !testServicesLoaded) void loadTestServices();
  }, [testServiceId, testServicesLoaded, loadTestServices]);
  const [newChoice, setNewChoice] = useState<runtimeApi.RuntimeChoice | null>(null);
  const [runtimeProfiles, setRuntimeProfiles] = useState<runtimeApi.RuntimeProfile[]>([]);
  const [runtimeRunStates, setRuntimeRunStates] = useState<Record<string, string>>({});
  const [runtimeQueues, setRuntimeQueues] = useState<RuntimeQueues>({});
  const runtimeQueuesRef = useRef(runtimeQueues);
  runtimeQueuesRef.current = runtimeQueues;
  const runtimeQueueStorageKeyRef = useRef<string | null>(null);
  const commitRuntimeQueues = useCallback((update: (previous: RuntimeQueues) => RuntimeQueues) => {
    const next = update(runtimeQueuesRef.current);
    runtimeQueuesRef.current = next;
    setRuntimeQueues(next);
    if (runtimeQueueStorageKeyRef.current) writeRuntimeQueues(runtimeQueueStorageKeyRef.current, next);
  }, []);
  const runtimeQueueDispatchingRef = useRef(new Set<string>());
  const [runtimeQueueSending, setRuntimeQueueSending] = useState<Record<string, boolean>>({});
  const runtimeQueueRetryAtRef = useRef(new Map<string, number>());
  const [runtimeQueueErrors, setRuntimeQueueErrors] = useState<Record<string, string>>({});
  const [runtimeRefreshToken, setRuntimeRefreshToken] = useState<Record<string, number>>({});
  const [runtimeInitialSubmission, setRuntimeInitialSubmission] = useState<RuntimeInitialSubmission | null>(null);
  const [runtimePending, setRuntimePending] = useState<RuntimePendingTurns>({});
  const runtimePendingRef = useRef(runtimePending);
  runtimePendingRef.current = runtimePending;
  const runtimePendingStorageKeyRef = useRef<string | null>(null);
  const commitRuntimePending = useCallback((update: (previous: RuntimePendingTurns) => RuntimePendingTurns): boolean => {
    const key = runtimePendingStorageKeyRef.current;
    if (!key) return false;
    const next = update(runtimePendingRef.current);
    if (!writeRuntimePending(key, next)) return false;
    runtimePendingRef.current = next;
    setRuntimePending(next);
    return true;
  }, []);
  const reserveRuntimePending = useCallback((item: RuntimePendingTurn): boolean => {
    const existing = runtimePendingRef.current[item.conversationId];
    if (existing && existing.requestId !== item.requestId) return false;
    return commitRuntimePending(previous => ({ ...previous, [item.conversationId]: item }));
  }, [commitRuntimePending]);
  const resolveRuntimePending = useCallback((conversationId: string, requestId: string, run?: runtimeApi.RuntimeRun) => {
    if (runtimePendingRef.current[conversationId]?.requestId === requestId) {
      commitRuntimePending(previous => {
        if (previous[conversationId]?.requestId !== requestId) return previous;
        const next = { ...previous };
        delete next[conversationId];
        return next;
      });
    }
    if (run) setRuntimeInitialSubmission(previous => previous?.requestId === requestId
      ? { ...previous, status: 'submitted', run } : previous);
  }, [commitRuntimePending]);
  const runtimeRunStatesRef = useRef(runtimeRunStates);
  runtimeRunStatesRef.current = runtimeRunStates;
  const runtimeSessionForIdRef = useRef(new Map<string, string>());
  const testModeConvIdsRef = useRef(new Set<string>());
  testModeConvIdsRef.current = new Set(conversations.filter(conv => conv.test_service_id).map(conv => conv.id));
  const runtimeScannedVersionRef = useRef(new Map<string, string>());
  const runtimePollBusyRef = useRef(false);
  useEffect(() => {
    let disposed = false;
    api.getMe().then(user => {
      if (disposed) return;
      const key = runtimeQueueStorageKey(user.user_id);
      const saved = readRuntimeQueues(key);
      runtimeQueueStorageKeyRef.current = key;
      const pendingKey = runtimePendingStorageKey(user.user_id);
      runtimePendingStorageKeyRef.current = pendingKey;
      const savedPending = readRuntimePending(pendingKey);
      runtimePendingRef.current = savedPending;
      setRuntimePending(savedPending);
      commitRuntimeQueues(current => {
        const merged: RuntimeQueues = { ...saved };
        for (const [convId, items] of Object.entries(current)) {
          const existing = merged[convId] || [];
          merged[convId] = [...existing.filter(row => !items.some(item => item.id === row.id)), ...items];
        }
        return merged;
      });
    }).catch(() => { /* the chat still works without session storage */ });
    const persistOnExit = () => {
      const key = runtimeQueueStorageKeyRef.current;
      if (key) writeRuntimeQueues(key, runtimeQueuesRef.current);
      const pendingKey = runtimePendingStorageKeyRef.current;
      if (pendingKey) writeRuntimePending(pendingKey, runtimePendingRef.current);
    };
    window.addEventListener('pagehide', persistOnExit);
    return () => { disposed = true; window.removeEventListener('pagehide', persistOnExit); persistOnExit(); };
  }, [commitRuntimeQueues]);
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
  // A new DeepAgents conversation needs a server ID before /chat can start.
  // Keep its first turn visible while that creation request is still pending.
  const [creatingDeepagentSeq, setCreatingDeepagentSeq] = useState<number | null>(null);
  const sendingDeepagentRef = useRef(false);
  const pendingDeepagentSendsRef = useRef(new Map<string, DeferredDeepagentSend>());
  const [failedDeepagentSends, setFailedDeepagentSends] = useState<Record<string, DeferredDeepagentSend>>({});
  const [messages, setMessages] = useState<Message[]>([]);
  const [tracingOpen, setTracingOpen] = useState(false);
  const [traceFullscreen, setTraceFullscreen] = useState(false);
  const [traceRefreshToken, setTraceRefreshToken] = useState(0);
  const [runtimeTrace, setRuntimeTrace] = useState<RuntimeTraceSnapshot | null>(null);
  const [traceFilePanelInset, setTraceFilePanelInset] = useState(0);
  const [loadingConv, setLoadingConv] = useState(false);
  useEffect(() => { if (!currentConvId) setTracingOpen(false); }, [currentConvId]);
  useEffect(() => { if (!tracingOpen || !currentConvId) setTraceFullscreen(false); }, [tracingOpen, currentConvId]);
  useEffect(() => {
    if (!traceFullscreen) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setTraceFullscreen(false);
    };
    window.addEventListener('keydown', onEscape);
    return () => window.removeEventListener('keydown', onEscape);
  }, [traceFullscreen]);
  useEffect(() => {
    if (!fileBrowserOpen) { setTraceFilePanelInset(0); return; }
    const compact = window.matchMedia('(min-width: 768px) and (max-width: 1199px)');
    const panel = document.querySelector<HTMLElement>('[data-jf-file-panel]');
    if (!panel) return;
    const updateInset = () => setTraceFilePanelInset(compact.matches ? panel.getBoundingClientRect().width : 0);
    const observer = new ResizeObserver(updateInset);
    observer.observe(panel);
    compact.addEventListener('change', updateInset);
    updateInset();
    return () => { observer.disconnect(); compact.removeEventListener('change', updateInset); };
  }, [fileBrowserOpen]);
  useEffect(() => {
    if (!tracingOpen || !runtimeSessionId) return;
    const sid = runtimeSessionId;
    const controller = new AbortController();
    let busy = false;
    setRuntimeTrace({ sid, runs: [], loading: true, error: '' });
    const refresh = async () => {
      if (busy) return;
      busy = true;
      try {
        const session = await runtimeApi.session(sid, controller.signal);
        if (!controller.signal.aborted) setRuntimeTrace({ sid, runs: session.runs, loading: false, error: '' });
      } catch (error) {
        if (!controller.signal.aborted) setRuntimeTrace(previous => ({
          sid, runs: previous?.sid === sid ? previous.runs : [], loading: false,
          error: error instanceof Error ? error.message : String(error),
        }));
      } finally {
        busy = false;
      }
    };
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 4000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [tracingOpen, runtimeSessionId, traceRefreshToken]);
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

  const updateRuntimeRunState = useCallback((convId: string, sid: string, status: string | null) => {
    if (runtimeSessionForIdRef.current.get(convId) !== sid) return;
    setRuntimeRunStates(prev => {
      if (!status || runtimeApi.terminal(status)) {
        if (!(convId in prev)) return prev;
        const next = { ...prev };
        delete next[convId];
        return next;
      }
      return prev[convId] === status ? prev : { ...prev, [convId]: status };
    });
  }, []);

  useEffect(() => {
    const sessions = new Map(conversations.filter(c => c.runtime_session_id).map(c => [c.id, c.runtime_session_id!]));
    runtimeSessionForIdRef.current = sessions;
    for (const id of runtimeScannedVersionRef.current.keys()) {
      if (!sessions.has(id)) runtimeScannedVersionRef.current.delete(id);
    }
    setRuntimeRunStates(prev => {
      const next = Object.fromEntries(Object.entries(prev).filter(([id]) => sessions.has(id)));
      return Object.keys(next).length === Object.keys(prev).length ? prev : next;
    });
  }, [conversations]);

  const onCurrentRuntimeRunState = useCallback((status: string | null) => {
    if (currentConvId && runtimeSessionId) updateRuntimeRunState(currentConvId, runtimeSessionId, status);
  }, [currentConvId, runtimeSessionId, updateRuntimeRunState]);

  // ── @ 文件提及（仅 admin /chat） ──────────────────────────────────
  // 文件索引 lazy-load：第一次 @ 触发时拉一次，之后缓存到这里。
  // 整个文件树通常只有几百到几千条；前端做 in-memory fuzzy。
  const [fileIndex, setFileIndex] = useState<FileIndexEntry[]>([]);
  const [fileIndexMeta, setFileIndexMeta] = useState({ loaded: false, truncated: false });
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
      setFileIndexMeta({ loaded: true, truncated: data.truncated });
      fileIndexLoadedRef.current = true;
    } catch { /* silent — picker will just show empty */ }
    finally { fileIndexLoadingRef.current = false; }
  }, []);
  useEffect(() => { if (tracingOpen) void ensureFileIndex(); }, [tracingOpen, ensureFileIndex]);

  const currentQueue = currentConvId ? (queryQueues[currentConvId] ?? []) : [];
  const testModeBusy = testModeUpdating || loadingConv || creatingConversation.current || !!(currentConvId && (
    (isStreaming && streamingConvId === currentConvId)
    || (!!interruptData && streamingConvId === currentConvId)
    || serverStreaming.includes(currentConvId)
    || serverInterrupted.includes(currentConvId)
    || runtimeRunStates[currentConvId]
    || runtimeQueueSending[currentConvId]
    || runtimePending[currentConvId]
    || (runtimeQueues[currentConvId] || []).some(item => item.content.trim())
    || currentQueue.some(item => item.content.trim())
  ));
  const isViewingStream = currentConvId === streamingConvId;
  const viewingActiveStream = isStreaming && isViewingStream;
  const hitlOnCurrent = !!interruptData && isViewingStream;
  const allowInputWhileRunning = viewingActiveStream || hitlOnCurrent;
  const showStreamBlocks = isViewingStream && (isStreaming || streamBlocks.length > 0);

  // The ref closes the gap between a click and React's next render. Once the
  // stream state is rendered, the normal mid-run queue owns later sends.
  useEffect(() => {
    if (isStreaming) sendingDeepagentRef.current = false;
  }, [isStreaming]);

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
      if (convId === currentConvIdRef.current && state.has_interrupt && state.actions) {
        restoreInterrupt(convId, { actions: state.actions, configs: state.configs });
      }
    } catch { /* ignore */ }
  }, [isStreaming, interruptData, restoreInterrupt]);

  useEffect(() => {
    loadConversations();
    loadProjects();
    loadModels();
    checkServerStreaming();
  }, []);

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        checkServerStreaming();
        void loadConversations();
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
      runtimeSessionForIdRef.current = new Map(convs.filter(c => c.runtime_session_id).map(c => [c.id, c.runtime_session_id!]));
      setConversations(convs);
      setRuntimeRunStates(prev => {
        const next = Object.fromEntries(Object.entries(prev).filter(([id]) => runtimeSessionForIdRef.current.has(id)));
        return Object.keys(next).length === Object.keys(prev).length ? prev : next;
      });
      void scanRecentRuntimeSessions(convs);
    } catch (e: unknown) {
      setListError(e instanceof Error ? e.message : t('chat.loadConvFail'));
    } finally {
      setListLoading(false);
    }
  }

  async function loadProjects() {
    try {
      const data = await api.listProjects();
      setProjects(data);
      setProjectListError('');
    } catch (error) {
      setProjectListError(error instanceof Error ? error.message : t('projects.loadFailed'));
    }
  }

  function navigateChat(projectId: string | null, conversationId?: string | null, newChat = false) {
    const params = new URLSearchParams();
    if (projectId) params.set('project', projectId);
    if (conversationId) params.set('conversation', conversationId);
    if (newChat) params.set('new', '1');
    navigate(params.size ? `/?${params}` : '/');
  }

  async function createProject() {
    const name = projectName.trim();
    if (!name || creatingProject) return;
    setCreatingProject(true);
    try {
      const project = await api.createProject(name);
      setProjects(previous => [...previous, project]);
      setProjectDialogOpen(false);
      setProjectName('');
      handleNewChat();
      navigateChat(project.id);
      closeNavigation();
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : t('projects.createFailed'));
    } finally {
      setCreatingProject(false);
    }
  }

  async function moveConversation(conv: Conversation, projectId: string | null) {
    if ((conv.project_id || null) === projectId) return;
    try {
      const updated = await api.moveConversation(conv.id, projectId);
      setConversations(previous => previous.map(item => item.id === conv.id ? { ...item, ...updated, project_id: projectId } : item));
      if (currentConvIdRef.current === conv.id) navigateChat(projectId, conv.id);
      messageApi.success(t('projects.moved'));
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : t('projects.moveFailed'));
    }
  }

  async function scanRecentRuntimeSessions(convs: Conversation[]) {
    // Active or queued runs update the conversation timestamp, so the newest
    // sessions cover the normal reload path without reading every old run.
    const candidates = convs.filter(c => c.runtime_session_id).filter(c => {
      const version = `${c.runtime_session_id}:${c.updated_at}`;
      if (runtimeScannedVersionRef.current.get(c.id) === version) return false;
      runtimeScannedVersionRef.current.set(c.id, version);
      return true;
    });
    let cursor = 0;
    await Promise.all(Array.from({ length: Math.min(3, candidates.length) }, async () => {
      while (cursor < candidates.length) {
        const conv = candidates[cursor++];
        const sid = conv.runtime_session_id!;
        const version = `${sid}:${conv.updated_at}`;
        try {
          const session = await readRuntimeSession(sid);
          if (runtimeScannedVersionRef.current.get(conv.id) === version) {
            const active = [...session.runs].reverse().find(run => !runtimeApi.terminal(run.status));
            updateRuntimeRunState(conv.id, sid, active?.status || null);
            if (currentConvIdRef.current === conv.id) setRuntimeRefreshToken(prev => ({ ...prev, [conv.id]: (prev[conv.id] || 0) + 1 }));
          }
        } catch {
          if (runtimeScannedVersionRef.current.get(conv.id) === version) runtimeScannedVersionRef.current.delete(conv.id);
        }
      }
    }));
  }

  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      if (runtimePollBusyRef.current) return;
      const targets = Object.keys(runtimeRunStatesRef.current)
        .filter(id => id !== currentConvIdRef.current)
        .map(id => ({ id, sid: runtimeSessionForIdRef.current.get(id) }))
        .filter((entry): entry is { id: string; sid: string } => !!entry.sid);
      if (!targets.length) return;
      runtimePollBusyRef.current = true;
      try {
        await Promise.all(targets.map(async ({ id, sid }) => {
          try {
            const session = await readRuntimeSession(sid);
            if (disposed || id === currentConvIdRef.current) return;
            const active = [...session.runs].reverse().find(run => !runtimeApi.terminal(run.status));
            updateRuntimeRunState(id, sid, active?.status || null);
          } catch { /* preserve the last known state until the next poll */ }
        }));
      } finally { runtimePollBusyRef.current = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [updateRuntimeRunState]);

  // The runtime service accepts one unfinished run per session. Keep follow-ups
  // in session storage so route changes/reloads cannot silently drop them.
  useEffect(() => {
    let disposed = false;
    const dispatch = async () => {
      const queued = Object.entries(runtimeQueuesRef.current).filter(([, items]) => items.length);
      await Promise.all(queued.map(async ([convId]) => {
        if (runtimeQueueDispatchingRef.current.has(convId) || Date.now() < (runtimeQueueRetryAtRef.current.get(convId) || 0)) return;
        if (testModeConvIdsRef.current.has(convId)) return;
        const sid = runtimeSessionForIdRef.current.get(convId);
        if (!sid) return;
        runtimeQueueDispatchingRef.current.add(convId);
        try {
          const snapshot = await readRuntimeSession(sid);
          if (disposed || runtimeSessionForIdRef.current.get(convId) !== sid || testModeConvIdsRef.current.has(convId)) return;
          const acknowledged = new Set(snapshot.runs.map(run => run.request_id).filter(Boolean));
          if ((runtimeQueuesRef.current[convId] || []).some(row => acknowledged.has(row.id))) {
            commitRuntimeQueues(prev => ({ ...prev, [convId]: (prev[convId] || []).filter(row => !acknowledged.has(row.id)) }));
            setRuntimeRefreshToken(prev => ({ ...prev, [convId]: (prev[convId] || 0) + 1 }));
          }
          const activeRun = [...snapshot.runs].reverse().find(run => !runtimeApi.terminal(run.status));
          if (activeRun) { updateRuntimeRunState(convId, sid, activeRun.status); return; }
          updateRuntimeRunState(convId, sid, null);
          const item = runtimeQueuesRef.current[convId]?.find(row => row.content.trim());
          if (!item) return;
          setRuntimeQueueSending(prev => ({ ...prev, [convId]: true }));
          commitRuntimeQueues(prev => ({ ...prev, [convId]: (prev[convId] || []).map(row => row.id === item.id ? { ...row, attempted: true } : row) }));
          try {
            const run = await runtimeApi.turn(convId, item.id, item.content.trim(), item.model, [], item.yolo);
            if (disposed) return;
            commitRuntimeQueues(prev => ({ ...prev, [convId]: (prev[convId] || []).filter(row => row.id !== item.id) }));
            updateRuntimeRunState(convId, sid, run.status);
            setRuntimeRefreshToken(prev => ({ ...prev, [convId]: (prev[convId] || 0) + 1 }));
            setRuntimeQueueErrors(prev => { const next = { ...prev }; delete next[convId]; return next; });
            void loadConversations();
          } catch (error) {
            if (disposed || runtimeSessionForIdRef.current.get(convId) !== sid) return;
            // POST may have succeeded even if its response was lost. The same
            // request ID can be retried safely after a failed status read.
            let accepted: runtimeApi.RuntimeRun | undefined;
            try { accepted = (await readRuntimeSession(sid)).runs.find(run => run.request_id === item.id); } catch { /* retry with same request ID */ }
            if (accepted) {
              commitRuntimeQueues(prev => ({ ...prev, [convId]: (prev[convId] || []).filter(row => row.id !== item.id) }));
              updateRuntimeRunState(convId, sid, accepted.status);
              setRuntimeRefreshToken(prev => ({ ...prev, [convId]: (prev[convId] || 0) + 1 }));
              void loadConversations();
            } else {
              const status = (error as { status?: number }).status;
              runtimeQueueRetryAtRef.current.set(convId, status && ![409, 429, 503].includes(status) ? Infinity : Date.now() + 5000);
              setRuntimeQueueErrors(prev => ({ ...prev, [convId]: error instanceof Error ? error.message : '排队消息发送失败' }));
            }
          }
        } catch (error) {
          runtimeQueueRetryAtRef.current.set(convId, Date.now() + 5000);
          setRuntimeQueueErrors(prev => ({ ...prev, [convId]: error instanceof Error ? error.message : '无法读取运行状态' }));
        } finally {
          runtimeQueueDispatchingRef.current.delete(convId);
          setRuntimeQueueSending(prev => { const next = { ...prev }; delete next[convId]; return next; });
        }
      }));
    };
    void dispatch();
    const timer = window.setInterval(() => void dispatch(), 2500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [updateRuntimeRunState, commitRuntimeQueues]);

  function changeRuntimeQueue(convId: string, items: RuntimeQueuedTurn[]) {
    commitRuntimeQueues(prev => ({ ...prev, [convId]: items }));
    runtimeQueueRetryAtRef.current.delete(convId);
    setRuntimeQueueErrors(prev => { const next = { ...prev }; delete next[convId]; return next; });
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
  const viewingDeepagentCreation = creatingDeepagentSeq !== null
    && creatingDeepagentSeq === loadMessagesRef.current && !currentConvId;
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
    setTestServiceId(null);
    setTestPickerOpen(false);
    setMessages([]);
    setLoadingConv(true);
    setConversationError('');
    try {
      const detail = await api.getConversation(convId);
      if (seq !== loadMessagesRef.current) return;
      setMessages(detail.messages || []);
      setRuntimeSessionId(detail.runtime_session_id || null);
      setRuntimeBinding(detail.runtime_binding);
      setTestServiceId(detail.test_service_id || null);
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

  useEffect(() => {
    if (routeConversationId) {
      if (routeConversationId !== currentConvIdRef.current) void loadMessages(routeConversationId);
    } else if (currentConvIdRef.current) {
      handleNewChat();
    }
    // The URL is the deep-link and browser-history source of truth for selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search]);

  function openConversation(conv: Conversation) {
    const projectId = conv.project_id && projects.some(project => project.id === conv.project_id) ? conv.project_id : null;
    navigateChat(projectId, conv.id);
    void loadMessages(conv.id);
    closeNavigation();
  }

  function openProject(projectId: string | null) {
    handleNewChat();
    navigateChat(projectId);
    closeNavigation();
  }

  function startNewChat(projectId: string | null = selectedProjectId) {
    handleNewChat();
    navigateChat(projectId, null, true);
    closeNavigation();
  }

  function handleNewChat() {
    ++loadMessagesRef.current;
    setConversationError('');
    setLoadingConv(false);
    setCurrentConvId(null);
    setRuntimeSessionId(null);
    setRuntimeBinding(null);
    setTestServiceId(null);
    setTestPickerOpen(false);
    setMessages([]);
    setInputValue('');
    setAttachedImages([]);
    fileTokenInputRef.current?.clear();
    setMention({ active: false, triggerStart: -1, query: '', activeIndex: 0 });
  }

  function openTestPicker() {
    setSelectedTestServiceId(testServiceId);
    setTestPickerOpen(true);
    void loadTestServices();
  }

  async function applyTestMode(serviceId: string | null) {
    if (testModeUpdatingRef.current) return;
    if (testModeBusy) {
      messageApi.warning(t('chat.serviceTestBusy'));
      return;
    }
    if (!currentConvId) {
      setTestServiceId(serviceId);
      setTestPickerOpen(false);
      return;
    }
    if (serviceId === testServiceId) {
      setTestPickerOpen(false);
      return;
    }
    const convId = currentConvId;
    testModeUpdatingRef.current = true;
    setTestModeUpdating(true);
    try {
      if (serviceId && runtimeSessionId) {
        const session = await readRuntimeSession(runtimeSessionId);
        if (session.runs.some(run => !runtimeApi.terminal(run.status))) {
          messageApi.warning(t('chat.serviceTestBusy'));
          return;
        }
      }
      const updated = await api.setConversationTestMode(convId, serviceId);
      setConversations(previous => previous.map(conv => conv.id === convId
        ? { ...conv, test_service_id: updated.test_service_id || null } : conv));
      if (currentConvIdRef.current === convId) {
        setTestServiceId(updated.test_service_id || null);
        setTestPickerOpen(false);
        try {
          const detail = await api.getConversation(convId);
          if (currentConvIdRef.current === convId) setMessages(detail.messages || []);
        } catch { /* existing messages remain visible; next refresh reconciles them */ }
      }
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : t('chat.serviceTestSwitchFailed'));
    } finally {
      testModeUpdatingRef.current = false;
      setTestModeUpdating(false);
    }
  }

  async function handleDeleteConv(convId: string) {
    try {
      let stopped: StreamIdentity | null = null;
      let status: api.StopChatResult['status'] | undefined;
      if (convId === streamingConvId && (isStreaming || interruptData)) {
        const outcome = await stopStream();
        if (!outcome) return;
        stopped = outcome.identity;
        status = outcome.pending ? 'stopping' : outcome.status;
      } else if (serverStreaming.includes(convId) || serverInterrupted.includes(convId)) {
        status = (await api.stopChat(convId)).status;
      }
      if (status === 'stopping') {
        messageApi.info(t('chat.stopBeforeDelete'));
        void checkServerStreaming();
        return;
      }
      await api.deleteConversation(convId);
      if (stopped) clearFinished(stopped);
      runtimeSessionForIdRef.current.delete(convId);
      pendingDeepagentSendsRef.current.delete(convId);
      setFailedDeepagentSends(prev => { const next = { ...prev }; delete next[convId]; return next; });
      runtimeQueueRetryAtRef.current.delete(convId);
      commitRuntimeQueues(prev => { const next = { ...prev }; delete next[convId]; return next; });
      commitRuntimePending(prev => { const next = { ...prev }; delete next[convId]; return next; });
      setRuntimeQueueErrors(prev => { const next = { ...prev }; delete next[convId]; return next; });
      setRuntimeInitialSubmission(prev => prev?.conversationId === convId ? null : prev);
      setConversations((prev) => prev.filter((c) => c.id !== convId));
      if (currentConvIdRef.current === convId) {
        ++loadMessagesRef.current;
        setLoadingConv(false);
        setConversationError('');
        setCurrentConvId(null);
        setRuntimeSessionId(null);
        setRuntimeBinding(null);
        setTestServiceId(null);
        setTestPickerOpen(false);
        setMessages([]);
        navigateChat(selectedProjectId);
      }
    } catch (e: unknown) {
      messageApi.error(e instanceof Error ? e.message : t('chat.deleteFail'));
    }
  }

  function handleStreamDone(convId: string, identity: StreamIdentity, continueQueue = true) {
    loadConversations();
    checkServerStreaming();
    api.getConversation(convId).then((detail) => {
      if (!isCurrentStream(identity)) return;
      pendingDeepagentSendsRef.current.delete(convId);
      if (convId === currentConvIdRef.current) {
        setMessages(detail.messages || []);
      }
      if (clearFinished(identity) && continueQueue) processNextQueuedMessage(convId);
    }).catch(() => {});
  }

  function removeQueueItem(convId: string, itemId?: string) {
    setQueryQueues((prev) => {
      const list = prev[convId] ?? [];
      const next = itemId ? list.filter((i) => i.id !== itemId) : list;
      return { ...prev, [convId]: next };
    });
  }

  const handleRunContinued = useCallback((convId: string, _content: string, queueId: string | undefined, identity: StreamIdentity) => {
    if (queueId) removeQueueItem(convId, queueId);
    api.getConversation(convId).then((detail) => {
      if (!isCurrentStream(identity)) return;
      if (convId === currentConvIdRef.current) {
        setMessages(detail.messages || []);
      }
    }).catch(() => {});
  }, [isCurrentStream]);

  async function runInterruptItem(convId: string, item: QueryQueueItem) {
    const trimmed = item.content.trim();
    if (!trimmed) return;
    const generation = getStreamGeneration();
    try {
      const result = await api.stopChat(convId, { followUp: trimmed, queueId: item.id });
      if (generation !== getStreamGeneration()) return;
      if (result.status !== 'stopping') messageApi.warning(t('chat.interruptNotRunning'));
      // Only the server's run_continued receipt removes this queued item.
    } catch (e: unknown) {
      if (generation !== getStreamGeneration()) return;
      messageApi.error(e instanceof Error ? e.message : t('chat.interruptFail'));
    }
  }

  function buildStreamOpts() {
    if (testServiceId) return {
      onDone: handleStreamDone,
      onError: handleStreamError,
      onRunContinued: handleRunContinued,
    };
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

  function handleStreamError(convId: string, _msg: string, identity: StreamIdentity) {
    setTimeout(() => {
      if (!isCurrentStream(identity)) return;
      api.getConversation(convId).then((detail) => {
        if (!isCurrentStream(identity)) return;
        const pending = pendingDeepagentSendsRef.current.get(convId);
        pendingDeepagentSendsRef.current.delete(convId);
        if (pending && detail.messages.filter(message => message.role === 'user').length <= pending.previousUserCount) {
          setFailedDeepagentSends(prev => ({ ...prev, [convId]: pending }));
        }
        if (convId === currentConvIdRef.current) {
          setMessages(detail.messages || []);
        }
        clearFinished(identity);
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
    if (!isStreaming && !interruptData) return;
    const outcome = await stopStream();
    if (!outcome || !isCurrentStream(outcome.identity)) return;
    if (outcome.pending) return; // SSE terminal event owns cleanup.
    const identity = outcome.identity;
    resetScroll();
    try {
      const detail = await api.getConversation(identity.conversationId);
      if (!isCurrentStream(identity)) return;
      if (identity.conversationId === currentConvIdRef.current) setMessages(detail.messages || []);
      clearFinished(identity);
      loadConversations();
      checkServerStreaming();
    } catch { /* The acknowledged stop remains visible if history is unavailable. */ }
  }

  async function handleForceStop(convId: string) {
    const generation = getStreamGeneration();
    try {
      await api.stopChat(convId);
      if (generation !== getStreamGeneration()) return;
      messageApi.info(t('chat.stopRequested'));
      setTimeout(async () => {
        if (generation !== getStreamGeneration()) return;
        try {
          await checkServerStreaming();
          const detail = await api.getConversation(convId);
          if (generation !== getStreamGeneration()) return;
          if (convId === currentConvIdRef.current) setMessages(detail.messages || []);
          loadConversations();
        } catch { /* The server-status banner retains the retry/refresh controls. */ }
      }, 1000);
    } catch (error: unknown) {
      if (generation !== getStreamGeneration()) return;
      messageApi.error(error instanceof Error ? error.message : t('chat.abortPrevFail'));
    }
  }

  function navigateToStreamingConv() {
    if (!streamingConvId) return;
    const conversation = conversations.find(item => item.id === streamingConvId);
    if (conversation) openConversation(conversation);
    else {
      navigateChat(null, streamingConvId);
      void loadMessages(streamingConvId);
    }
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
    if (loadingConv || (runtimeSessionId && !testServiceId) || conversationError || testModeUpdatingRef.current) return;
    const msg = (text ?? inputValue).trim();
    const hasImages = attachedImages.length > 0;
    if (!msg && !hasImages) return;
    if (creatingConversation.current) return;

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

    // Lock before changing state: a second click/Enter in the same render must
    // not send a second /chat request or abort the first stream.
    if (sendingDeepagentRef.current) return;
    if (currentConvId) sendingDeepagentRef.current = true;

    const submittedImages = attachedImages;
    const displayContent = hasImages
      ? (msg || '') + submittedImages.map((img) => `\n![${img.name}](${img.dataUrl})`).join('')
      : msg;
    const userMessage: Message = { role: 'user', content: displayContent };
    let messageContent: string | unknown[] = msg;
    if (hasImages) {
      const parts: unknown[] = [];
      if (msg) parts.push({ type: 'text', text: msg });
      parts.push(...submittedImages.map((img) => ({
        type: 'image_url', image_url: { url: img.dataUrl },
      })));
      messageContent = parts;
    }

    let convId = currentConvId;
    let optimisticCreation = false;
    if (!convId) {
      creatingConversation.current = true;
      let createdConvId: string | null = null;
      const creationSeq = loadMessagesRef.current;
      try {
        if (!catalogReady || catalogError) {
          messageApi.error(catalogError || '正在加载模型，请稍后发送');
          return;
        }
        const choice: runtimeApi.RuntimeChoice = newChoice?.runtime && newChoice.runtime !== 'deepagents'
          ? newChoice : { runtime: 'deepagents', model: selectedModel || undefined };
        if (!testServiceId && choice.runtime !== 'deepagents' && !runtimePendingStorageKeyRef.current) {
          messageApi.error('正在确认当前账号，暂不能安全保存待提交消息，请稍后重试');
          return;
        }
        const title = msg ? msg.slice(0, 30) : t('chat.imgConvTitle');
        if (choice.runtime === 'deepagents') {
          optimisticCreation = true;
          setCreatingDeepagentSeq(creationSeq);
          setMessages([userMessage]);
          setInputValue('');
          setAttachedImages([]);
          fileTokenInputRef.current?.clear();
          setMention({ active: false, triggerStart: -1, query: '', activeIndex: 0 });
          requestAnimationFrame(resetScroll);
        }
        const created = await api.createConversation(title, choice, [], selectedProjectId);
        createdConvId = created.id;
        const conv = testServiceId
          ? await api.setConversationTestMode(created.id, testServiceId)
          : created;
        setConversations((prev) => [conv, ...prev]);
        convId = conv.id;
        if (creationSeq === loadMessagesRef.current) {
          currentConvIdRef.current = conv.id;
          navigateChat(selectedProjectId, conv.id);
        }
        if (conv.runtime_session_id && !conv.test_service_id) {
          runtimeSessionForIdRef.current.set(conv.id, conv.runtime_session_id);
          const requestId = crypto.randomUUID();
          const submittedAttachments = submittedImages;
          const submission: RuntimeInitialSubmission = {
            conversationId: conv.id, requestId, text: msg, model: choice.model,
            yolo: getYoloMode(), attachments: submittedAttachments, status: 'sending',
          };
          if (!reserveRuntimePending({ conversationId: conv.id, requestId, text: msg,
            model: choice.model, yolo: submission.yolo, attachments: submittedAttachments, kind: 'initial' })) {
            messageApi.error('无法保存待确认消息；请减少附件或释放浏览器存储空间后重试，输入内容仍在');
            return;
          }
          if (creationSeq === loadMessagesRef.current) {
            setCurrentConvId(conv.id);
            setRuntimeSessionId(conv.runtime_session_id);
            setRuntimeBinding(conv.runtime_binding);
            setRuntimeInitialSubmission(submission);
            setInputValue('');
            setAttachedImages([]);
            fileTokenInputRef.current?.clear();
          }
          try {
            const run = await runtimeApi.turn(conv.id, requestId, msg, choice.model,
              submittedAttachments.map(f => ({ name: f.name, data_url: f.dataUrl })), submission.yolo);
            resolveRuntimePending(conv.id, requestId, run);
            setRuntimeInitialSubmission(prev => prev?.requestId === requestId ? { ...prev, status: 'submitted', run } : prev);
            updateRuntimeRunState(conv.id, conv.runtime_session_id, run.status);
            void loadConversations();
          } catch (error) {
            let accepted: runtimeApi.RuntimeRun | undefined;
            try { accepted = (await readRuntimeSession(conv.runtime_session_id)).runs.find(run => run.request_id === requestId); } catch { /* keep the same request ID for retry */ }
            if (accepted) {
              resolveRuntimePending(conv.id, requestId, accepted);
              setRuntimeInitialSubmission(prev => prev?.requestId === requestId ? { ...prev, status: 'submitted', run: accepted } : prev);
              updateRuntimeRunState(conv.id, conv.runtime_session_id, accepted.status);
              void loadConversations();
            } else {
              const detail = error instanceof Error ? error.message : '发送失败，请重试';
              setRuntimeInitialSubmission(prev => prev?.requestId === requestId ? { ...prev, status: 'failed', error: detail } : prev);
              messageApi.error(detail);
            }
          }
          return;
        }
        if (creationSeq === loadMessagesRef.current) {
          setCurrentConvId(convId);
          setRuntimeSessionId(conv.runtime_session_id || null);
          setRuntimeBinding(conv.runtime_binding);
          setTestServiceId(conv.test_service_id || null);
        }
      } catch (e) {
        if (createdConvId) void loadConversations();
        if (optimisticCreation && creationSeq === loadMessagesRef.current) {
          setMessages([]);
          setInputValue(msg);
          setAttachedImages(submittedImages);
          requestAnimationFrame(() => fileTokenInputRef.current?.focus());
        }
        messageApi.error(createdConvId && testServiceId
          ? t('chat.serviceTestPrepareFailed', { error: e instanceof Error ? e.message : t('chat.createConvFail') })
          : e instanceof Error ? e.message : t('chat.createConvFail'));
        return;
      } finally {
        creatingConversation.current = false;
        setCreatingDeepagentSeq(prev => prev === creationSeq ? null : prev);
      }
    }

    if (!optimisticCreation) {
      setMessages((prev) => [...prev, userMessage]);
      setInputValue('');
      fileTokenInputRef.current?.clear();
      setMention({ active: false, triggerStart: -1, query: '', activeIndex: 0 });
      resetScroll();
    }
    if (hasImages) {
      void cacheImages(convId, submittedImages);
      if (!optimisticCreation) setAttachedImages([]);
    }

    pendingDeepagentSendsRef.current.set(convId, {
      text: msg, images: submittedImages,
      previousUserCount: messages.filter(message => message.role === 'user').length,
    });
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
    : selectedProjectId ? `${projects.find(project => project.id === selectedProjectId)?.name || t('projects.title')} · ${t('chat.newChat')}`
      : t('ux.workspaceTitle');
  const activeTestService = testServices.find(service => service.id === testServiceId);
  const testServiceName = activeTestService?.name || testServiceId || '';
  const currentPending = currentConvId ? runtimePending[currentConvId] : undefined;
  const currentInitialSubmission: RuntimeInitialSubmission | undefined =
    runtimeInitialSubmission?.conversationId === currentConvId ? runtimeInitialSubmission
      : currentPending?.kind === 'initial'
        ? { ...currentPending, status: 'failed', error: '上次消息的提交状态待确认；刷新状态后可用原请求编号重试' }
        : undefined;


  const searchTerm = conversationSearch.trim().toLocaleLowerCase();
  const visibleConversations = conversations.filter(conv => !searchTerm || conv.title.toLocaleLowerCase().includes(searchTerm));
  const knownProjectIds = new Set(projects.map(project => project.id));
  const isUngroupedVisible = (conv: Conversation) => !conv.project_id || !knownProjectIds.has(conv.project_id);

  function renderConversation(conv: Conversation) {
    const runtimeState = runtimeRunStates[conv.id];
    const runtimeQueueCount = (runtimeQueues[conv.id] || []).filter(row => row.content.trim()).length;
    const runtimeLabel = runtimeState === 'queued' ? '等待共享连接' : runtimeState === 'starting' ? '正在准备会话' : t('chat.streamingTitle');
    const isConvStreaming = (!!runtimeState && runtimeState !== 'waiting_approval') || (conv.id === streamingConvId && isStreaming);
    const isConvHitl = runtimeState === 'waiting_approval' || (conv.id === streamingConvId && !!interruptData && !isStreaming);
    const moveItems = [
      { key: 'ungrouped', label: t('projects.ungrouped'), disabled: !conv.project_id },
      ...projects.map(project => ({ key: project.id, label: project.name, disabled: project.id === conv.project_id })),
    ];
    return <div key={conv.id} className={`${styles.convItem} ${conv.id === currentConvId ? styles.active : ''}`}>
      <button type="button" className={styles.convSelect} title={conv.title}
        aria-current={conv.id === currentConvId ? 'page' : undefined} onClick={() => openConversation(conv)}>
        {isConvStreaming && <span title={runtimeLabel} style={{ display: 'inline-flex' }}><RunIndicator state="running" label={runtimeLabel} /></span>}
        {isConvHitl && <span title={t('chat.hitlBadge')} style={{ display: 'inline-flex' }}><RunIndicator state="approval" label={t('chat.hitlBadge')} /></span>}
        <span className={styles.convTitle}>{conv.title}</span>
        {runtimeQueueCount > 0 && <span title={`待发送 ${runtimeQueueCount} 条`} style={{ flexShrink: 0, fontSize: 11, color: 'var(--jf-text-muted)' }}>待发送 {runtimeQueueCount}</span>}
      </button>
      <Dropdown menu={{ items: moveItems, onClick: ({ key, domEvent }) => { domEvent.stopPropagation(); void moveConversation(conv, key === 'ungrouped' ? null : key); } }} trigger={['click']}>
        <Button type="text" size="small" icon={<DotsThree size={16} />} className={styles.convDelete}
          aria-label={t('projects.moveConversation', { title: conv.title })} title={t('projects.moveConversation', { title: conv.title })}
          onClick={event => event.stopPropagation()} />
      </Dropdown>
      <Popconfirm title={t('ux.deleteTitle')} description={t('ux.deleteDescription')}
        okText={t('ux.delete')} cancelText={t('common.cancel')} okButtonProps={{ danger: true }} onConfirm={() => handleDeleteConv(conv.id)}>
        <Button type="text" size="small" danger icon={<Trash size={14} />} className={styles.convDelete}
          aria-label={t('chat.deleteConversation', { title: conv.title })} title={t('chat.deleteConversation', { title: conv.title })}
          onClick={event => event.stopPropagation()} />
      </Popconfirm>
    </div>;
  }

  const sidebarContent = (
    <div className={styles.chatContainer} style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent' }}>
      <div className={styles.sidebarHeader}>
        <Button
          className={styles.newChatBtn}
          icon={<Plus size={16} />}
          onClick={() => { setConversationSearch(''); startNewChat(); }}
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
      <div className={styles.convListLabel}>
        <span>{t('projects.title')}</span>
        <Button type="text" size="small" icon={<Plus size={14} />} aria-label={t('projects.create')}
          title={t('projects.create')} onClick={() => { setProjectName(''); setProjectDialogOpen(true); }} />
      </div>
      <nav aria-label={t('chat.conversationList')} className={styles.convList} aria-busy={listLoading}>
        {projectListError && <Alert type="error" message={projectListError}
          action={<Button size="small" onClick={() => void loadProjects()}>{t('ux.retry')}</Button>} />}
        {listError && <Alert type="error" message={t('chat.loadConvFail')} description={listError}
          action={<Button size="small" onClick={() => void loadConversations()}>{t('ux.retry')}</Button>} />}
        {listLoading && <div role="status" className={styles.convEmpty}>{t('ux.loading')}</div>}
        {projects.filter(project => !searchTerm || project.name.toLocaleLowerCase().includes(searchTerm)
          || visibleConversations.some(conv => conv.project_id === project.id)).map(project => {
          const groupConversations = visibleConversations.filter(conv => conv.project_id === project.id);
          const expanded = selectedProjectId === project.id || !!searchTerm;
          return <div key={project.id} className={styles.projectGroup}>
            <button type="button" className={`${styles.projectGroupButton} ${selectedProjectId === project.id ? styles.projectGroupActive : ''}`}
              onClick={() => openProject(project.id)} title={project.name}>
              <Folder size={17} /><span>{project.name}</span><small>{conversations.filter(conv => conv.project_id === project.id).length}</small>
            </button>
            {expanded && groupConversations.map(renderConversation)}
            {expanded && !searchTerm && groupConversations.length === 0 && <div className={styles.projectEmpty}>{t('projects.emptyConversations')}</div>}
          </div>;
        })}
        <div className={styles.projectGroup}>
          <button type="button" className={`${styles.projectGroupButton} ${!selectedProjectId ? styles.projectGroupActive : ''}`}
            onClick={() => openProject(null)}>
            <Folder size={17} /><span>{t('projects.ungrouped')}</span><small>{conversations.filter(isUngroupedVisible).length}</small>
          </button>
          {(!selectedProjectId || !!searchTerm) && visibleConversations.filter(isUngroupedVisible).map(renderConversation)}
        </div>
        {!listLoading && !listError && visibleConversations.length === 0 && (searchTerm || projects.length === 0) && (
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
      <div className={`${styles.chatArea} ${tracingOpen ? styles.chatAreaTracing : ''}`}>
        {isProjectOverview && selectedProjectId ? <ProjectOverview
          projectId={selectedProjectId}
          fallbackProject={projects.find(project => project.id === selectedProjectId)}
          conversations={conversations}
          onOpenConversation={conversationId => {
            const conversation = conversations.find(item => item.id === conversationId);
            if (conversation) openConversation(conversation);
            else { navigateChat(selectedProjectId, conversationId); void loadMessages(conversationId); closeNavigation(); }
          }}
          onNewConversation={() => startNewChat(selectedProjectId)}
          onChanged={() => void loadProjects()}
          onDeleted={() => {
            setProjects(previous => previous.filter(project => project.id !== selectedProjectId));
            setConversations(previous => previous.map(conv => conv.project_id === selectedProjectId ? { ...conv, project_id: null } : conv));
            openProject(null);
          }}
        /> : <>
        {/* 左侧 query 快速导航：悬浮在 chatArea 左侧垂直居中，脱离滚动容器，
            不随消息滚动消失；bar 数 = q 数，active 高亮，点击跳转。 */}
        {!tracingOpen && (!runtimeSessionId || !!testServiceId) && userMarkers.length > 0 && (
          <QueryNavigation items={userMarkers} activeId={String(activeQueryIndex)} onJump={jumpToQuery} />
        )}
        {/* Header */}
        <div className={styles.chatHeader} style={traceFilePanelInset ? { marginRight: traceFilePanelInset } : undefined}>
          <span className={styles.chatTitle}>{currentTitle}</span>
          <Popover
            open={testPickerOpen}
            onOpenChange={open => { if (!open) setTestPickerOpen(false); }}
            trigger="click"
            placement="bottomRight"
            content={<div className={styles.serviceTestPicker}>
              <strong>{t('chat.serviceTestTitle')}</strong>
              <p>{t('chat.serviceTestDescription')}</p>
              {testServicesError && <Alert type="warning" showIcon message={testServicesError}
                action={<Button size="small" onClick={() => void loadTestServices()}>{t('ux.retry')}</Button>} />}
              <Select
                aria-label={t('chat.serviceTestSelect')}
                showSearch optionFilterProp="label"
                placeholder={t('chat.serviceTestSelect')}
                value={selectedTestServiceId}
                onChange={value => setSelectedTestServiceId(value)}
                loading={testServicesLoading}
                disabled={testServicesLoading || testModeUpdating}
                options={testServices.map(service => ({
                  value: service.id,
                  label: `${service.name}${service.published ? '' : ` · ${t('chat.serviceTestDraft')}`}`,
                }))}
                style={{ width: '100%' }}
              />
              {!testServicesLoading && !testServicesError && testServices.length === 0 &&
                <p>{t('chat.serviceTestEmpty')}</p>}
              <div className={styles.serviceTestPickerActions}>
                <Button type="primary" size="small" loading={testModeUpdating}
                  disabled={!selectedTestServiceId || selectedTestServiceId === testServiceId || testModeBusy}
                  onClick={() => void applyTestMode(selectedTestServiceId)}>
                  {testServiceId ? t('chat.serviceTestSwitch') : t('chat.serviceTestStart')}
                </Button>
                {testServiceId && <Button size="small" disabled={testModeBusy}
                  onClick={() => void applyTestMode(null)}>{t('chat.serviceTestExit')}</Button>}
              </div>
              <small>{t('chat.serviceTestContactAdmin')}</small>
            </div>}
          >
            <button type="button" className={`${styles.serviceTestButton} ${testServiceId ? styles.serviceTestButtonActive : ''}`}
              aria-label={testServiceId ? t('chat.serviceTestActive', { name: testServiceName }) : t('chat.serviceTestTitle')}
              aria-pressed={!!testServiceId} disabled={testModeBusy}
              onClick={() => testPickerOpen ? setTestPickerOpen(false) : openTestPicker()}>
              <Flask size={16} />
              <span className={styles.serviceTestButtonText}>{testServiceId ? t('chat.serviceTestBadge') : t('chat.serviceTestTitle')}</span>
            </button>
          </Popover>
          <button type="button"
            className={`${styles.serviceTestButton} ${tracingOpen ? styles.serviceTestButtonActive : ''}`}
            aria-label={tracingOpen ? t('chat.traceShowConversation') : t('chat.traceToggle')}
            aria-pressed={tracingOpen}
            title={tracingOpen ? t('chat.traceShowConversation') : t('chat.traceToggle')}
            disabled={!currentConvId}
            onClick={() => setTracingOpen(open => !open)}>
            <GitBranch size={16} />
            <span className={styles.serviceTestButtonText}>Tracing</span>
          </button>
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

        {testServiceId && <div className={styles.serviceTestBanner} role="status"
          style={traceFilePanelInset ? { marginRight: traceFilePanelInset } : undefined}>
          <Flask size={16} />
          <span>{t(currentConvId ? 'chat.serviceTestActive' : 'chat.serviceTestPending', { name: testServiceName })}</span>
          <Button size="small" type="link" disabled={testModeBusy}
            onClick={() => void applyTestMode(null)}>{t('chat.serviceTestExit')}</Button>
        </div>}

        {(catalogError || modelsError) && <Alert type="warning" showIcon message={t('ux.modelsFailed')}
          description={catalogError || modelsError} action={<Button size="small" onClick={() => { setCatalogVersion(value => value + 1); void loadModels(); }}>{t('ux.retry')}</Button>} />}
        {tracingOpen && <div className={`${styles.traceViewport} ${traceFullscreen ? styles.traceViewportExpanded : ''}`} role="region" aria-label={t('chat.traceView')}
          style={traceFullscreen ? { right: traceFilePanelInset } : traceFilePanelInset ? { marginRight: traceFilePanelInset } : undefined}>
          {runtimeSessionId && runtimeTrace?.sid !== runtimeSessionId && <LogoLoading size={80} />}
          {runtimeTrace?.sid === runtimeSessionId && runtimeTrace.loading && <LogoLoading size={80} />}
          {runtimeTrace?.sid === runtimeSessionId && runtimeTrace.error &&
            <Alert type="warning" showIcon message={t('chat.traceLoadFailed')} description={runtimeTrace.error} />}
          {(!runtimeSessionId || (runtimeTrace?.sid === runtimeSessionId && !runtimeTrace.loading)) &&
            <TracingView key={currentConvId || 'new-conversation'} messages={messages} runtimeRuns={runtimeSessionId ? runtimeTrace?.runs || [] : undefined}
              traceTitle={t('chat.traceView')} workspaceFiles={fileIndex}
              workspaceIndexLoaded={fileIndexMeta.loaded} workspaceIndexTruncated={fileIndexMeta.truncated}
              fullscreen={traceFullscreen} onToggleFullscreen={() => setTraceFullscreen(value => !value)}
              onRefresh={() => {
                if (currentConvId) {
                  const convId = currentConvId;
                  void api.getConversation(convId).then(detail => {
                    if (currentConvIdRef.current === convId) setMessages(detail.messages || []);
                  }).catch(error => messageApi.error(error instanceof Error ? error.message : t('chat.loadMsgFail')));
                }
                if (runtimeSessionId) setTraceRefreshToken(value => value + 1);
              }}
              onOpenFile={path => { void revealInBrowser(path); }} />}
        </div>}
        {/* Messages */}
        {runtimeSessionId && !testServiceId && currentConvId ? <RuntimeConversation key={runtimeSessionId} sid={runtimeSessionId} conversationId={currentConvId} history={messages} profiles={runtimeProfiles} onChanged={loadConversations} onRunState={onCurrentRuntimeRunState}
          traceHidden={tracingOpen}
          pending={currentPending}
          onPendingStart={reserveRuntimePending}
          onPendingResolved={(requestId, run) => resolveRuntimePending(currentConvId, requestId, run)}
          queueItems={runtimeQueues[currentConvId] || []}
          queueInFlight={!!runtimeQueueSending[currentConvId]}
          onQueueSubmit={item => changeRuntimeQueue(currentConvId, [...(runtimeQueuesRef.current[currentConvId] || []), item])}
          onQueueChange={items => changeRuntimeQueue(currentConvId, items)}
          queueError={runtimeQueueErrors[currentConvId]}
          onQueueRetry={() => { runtimeQueueRetryAtRef.current.delete(currentConvId); setRuntimeQueueErrors(prev => { const next = { ...prev }; delete next[currentConvId]; return next; }); }}
          refreshToken={runtimeRefreshToken[currentConvId] || 0}
          onConfigure={() => navigate('/settings/environment')}
          initialSubmission={currentInitialSubmission} /> : <>
        <div className={styles.messagesViewport}>
        <div className={styles.messagesContainer} ref={setScrollParentEl}>
          {conversationError ? (
            <div className={styles.recoveryState}><Alert type="error" showIcon
              message={t('chat.loadMsgFail')} description={conversationError} />
              <Button type="primary" onClick={() => currentConvId && void loadMessages(currentConvId)}>{t('ux.retry')}</Button>
            </div>
          ) : loadingConv ? (
            <LogoLoading size={240} />
          ) : messages.length === 0 && !showStreamBlocks ? (
            <ChatWelcome onSuggest={prompt => { setInputValue(prompt); fileTokenInputRef.current?.focus(); }} onConfigure={() => navigate('/settings/environment')} />
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
              {(showStreamBlocks || viewingDeepagentCreation) && (
                <StreamingMessage blocks={viewingDeepagentCreation ? [] : streamBlocks}
                  isStreaming={viewingDeepagentCreation || isStreaming}
                  status={viewingDeepagentCreation ? 'starting' : interruptData ? 'waiting_approval' : isStreaming ? 'running' : 'completed'} />
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

        </div>
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

        {currentConvId && failedDeepagentSends[currentConvId] && (
          <Alert type="error" showIcon message={t('chat.sendNotDelivered')}
            description={inputValue.trim() || attachedImages.length > 0 ? t('chat.sendDraftFirst') : undefined}
            action={<Button size="small" disabled={!!inputValue.trim() || attachedImages.length > 0}
              onClick={() => {
                const failed = failedDeepagentSends[currentConvId];
                if (!failed) return;
                setInputValue(failed.text);
                setAttachedImages(failed.images);
                setFailedDeepagentSends(prev => { const next = { ...prev }; delete next[currentConvId]; return next; });
                requestAnimationFrame(() => fileTokenInputRef.current?.focus());
              }}>{t('chat.restoreToComposer')}</Button>} />
        )}

        {stopState !== 'idle' && streamingConvId && (
          <Alert showIcon type={stopState === 'failed' ? 'error' : 'info'}
            message={t(stopState === 'failed' ? 'chat.stopFailed' : stopState === 'requesting' ? 'chat.stopRequesting' : 'chat.stopRequested')}
            description={stopState === 'failed' ? stopError : t('chat.stopAwaitTerminal')}
            action={stopState === 'failed' ? <Button size="small" onClick={() => void handleStop()}>{t('chat.retryStop')}</Button> : undefined}
          />
        )}

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
                <Button size="small" type="link" danger loading={stopState === 'requesting'} onClick={handleStop}>
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
            canInterrupt={isStreaming && isViewingStream && !interruptData && stopState === 'idle'}
            hitlLocked={hitlOnCurrent}
          />
          <ChatComposer
            attachments={<ImageAttachment
              ref={imageAttachRef}
              allowFiles={!testServiceId && !currentConvId && !!newChoice && newChoice.runtime !== 'deepagents'}
              images={attachedImages}
              onImagesChange={setAttachedImages}
              disabled={viewingDeepagentCreation || (isStreaming && !allowInputWhileRunning)}
            />}
            tools={testServiceId || (!currentConvId && newChoice && newChoice.runtime !== 'deepagents') ? undefined : <><div className={styles.inputToolbarDivider} />{CAPABILITIES.map((cap) => (
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
            model={testServiceId ? <span className={styles.serviceTestModel}><Flask size={14} />{testServiceName}</span> : <ChatModelSelect
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
                  : testServiceId ? t('chat.serviceTestPlaceholder') : t('chat.composePlaceholder')
              }
              disabled={viewingDeepagentCreation || (isStreaming && !allowInputWhileRunning)}
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
            uploadDisabled={creatingDeepagentSeq !== null || isStreaming || attachedImages.length >= 5}
            hasAttachments={attachedImages.length > 0}
            onTranscript={text => void handleSend(text)} voiceDisabled={creatingDeepagentSeq !== null || (isStreaming && !allowInputWhileRunning)}
            onStop={viewingActiveStream ? () => void handleStop() : undefined}
            stopDisabled={stopState === 'requesting'}
            onSend={() => void handleSend()}
            sending={viewingDeepagentCreation}
            sendDisabled={loadingConv || !!conversationError || creatingDeepagentSeq !== null || (!inputValue.trim() && attachedImages.length === 0)
              || (isStreaming && !allowInputWhileRunning) || (!!interruptData && !hitlOnCurrent)
              || serverStreaming.includes(currentConvId ?? '')
              || (serverInterrupted.includes(currentConvId ?? '') && !hitlOnCurrent)}
          />
          {!testServiceId && yoloOn && currentConvId && yoloApprovedConvs.has(currentConvId) && (
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
        </>}
      </div>
      <Modal title={t('projects.create')} open={projectDialogOpen} onOk={() => void createProject()}
        onCancel={() => setProjectDialogOpen(false)} confirmLoading={creatingProject}
        okButtonProps={{ disabled: !projectName.trim() }} okText={t('common.create')} cancelText={t('common.cancel')}>
        <Input value={projectName} onChange={event => setProjectName(event.target.value)}
          onPressEnter={() => void createProject()} placeholder={t('projects.namePlaceholder')} maxLength={80} autoFocus />
      </Modal>
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

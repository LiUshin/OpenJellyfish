/**
 * Service-chat consumer-facing chat application.
 *
 * 与 admin /chat 共享：
 * - markdown.ts (含媒体标签处理 / sanitize 配置 / hljs 高亮)
 * - StreamingMessage 组件 (含 thinking / text / tool 区分渲染)
 * - StreamBlock 数据结构
 *
 * service 端专属：
 * - ServiceToolBadge (友好状态条，不展示 args/result)
 * - useServiceStream (轻量 SSE handler，无 subagent / interrupt / HITL)
 * - 欢迎屏 + 快速问题 chips
 * - API key from URL / localStorage
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import LanguageSwitcher from '../components/LanguageSwitcher';
import StreamingMessage from '../pages/Chat/components/StreamingMessage';
import QueryNavigation from '../pages/Chat/components/QueryNavigation';
import { answerPreview, plainPreview } from '../pages/Chat/utils/userQueryPreview';
import { setMediaUrlBuilder, setFileRevealEnabled, setFileDownloadMode } from '../pages/Chat/markdown';
import {
  AuthError,
  buildConsumerMediaUrl,
  clearStoredKey,
  consumeKeyFromUrl,
  createConversation,
  getConversation,
  getConversationEvents,
  getMediaToken,
  getServiceModels,
  getStoredByok,
  getStoredKey,
  isByokKey,
  loadConvStore,
  saveConvStore,
  setStoredByok,
  setStoredKey,
  type ByokCreds,
  type ConsumerMessage,
  type ConvMeta,
  type ServiceModelOption,
} from './serviceApi';
import type { StreamBlock } from '../pages/Chat/types';
import { useServiceStream } from './streamHandler';
import ServiceToolBadge from './ServiceToolBadge';
import GeneratedFilesPanel from './GeneratedFilesPanel';
import ConversationDrawer from './ConversationDrawer';
import styles from './serviceChat.module.css';

export interface ServiceConfig {
  service_id: string;
  service_name: string;
  service_desc?: string;
  welcome_message?: string;
  quick_questions?: string[];
}

interface UserMessage {
  text: string;
  images: string[];  // dataURL list
}

interface AssistantMessage {
  blocks: import('../pages/Chat/types').StreamBlock[];
}

interface PendingSend {
  id: string;
  text: string;
  images: { dataUrl: string; name: string }[];
  phase: 'creating' | 'streaming';
}

type MessageEntry = (
  | { kind: 'user'; data: UserMessage }
  | { kind: 'assistant'; data: AssistantMessage }
) & { id?: string; authorType?: string };

const MAX_PENDING_IMAGES = 5;

/** 从首条用户消息派生会话标题（截断）。 */
function makeTitle(text: string): string {
  const t = (text || '').trim().replace(/\s+/g, ' ');
  if (!t) return '';
  return t.length > 30 ? t.slice(0, 30) + '…' : t;
}

/** 把后端存储的 blocks 归一成 StreamBlock（补默认字段，历史 thinking 默认折叠）。 */
function normalizeBlocks(raw: unknown[]): StreamBlock[] {
  const out: StreamBlock[] = [];
  for (const item of raw) {
    const b = item as Record<string, unknown>;
    if (!b || typeof b !== 'object') continue;
    switch (b.type) {
      case 'text':
        out.push({ type: 'text', content: String(b.content ?? '') });
        break;
      case 'thinking':
        out.push({ type: 'thinking', content: String(b.content ?? ''), collapsed: b.collapsed !== false });
        break;
      case 'tool':
        out.push({
          type: 'tool',
          name: String(b.name ?? ''),
          args: String(b.args ?? ''),
          result: String(b.result ?? ''),
          done: b.done !== false,
          resultCollapsed: b.resultCollapsed !== false,
        });
        break;
      case 'subagent':
        out.push({ ...(b as object), collapsed: b.collapsed !== false, done: b.done !== false } as StreamBlock);
        break;
      default:
        break;
    }
  }
  return out;
}

/** 后端历史消息 → 前端可渲染的 MessageEntry（跳过空/系统消息）。 */
function backendMsgToEntry(m: ConsumerMessage): MessageEntry | null {
  if (m.role === 'user') {
    return { kind: 'user', data: { text: m.content ?? '', images: [] } };
  }
  if (m.role === 'assistant') {
    let blocks: StreamBlock[];
    if (Array.isArray(m.blocks) && m.blocks.length > 0) {
      blocks = normalizeBlocks(m.blocks);
    } else {
      blocks = [];
      for (const tc of m.tool_calls ?? []) {
        blocks.push({
          type: 'tool',
          name: tc.name ?? '',
          args: tc.args ?? '',
          result: tc.result ?? '',
          done: true,
          resultCollapsed: true,
        });
      }
      if (m.content) blocks.push({ type: 'text', content: m.content });
    }
    if (blocks.length === 0) return null;
    return { kind: 'assistant', data: { blocks }, id: m.event_id || m.message_id, authorType: m.author_type };
  }
  return null;
}

export default function ServiceChatApp({ config }: { config: ServiceConfig }) {
  const { t } = useTranslation();
  const [apiKey, setApiKey] = useState<string>(() => {
    const fromUrl = consumeKeyFromUrl(config.service_id);
    return fromUrl ?? getStoredKey(config.service_id);
  });
  const [authError, setAuthError] = useState<string>('');
  const [keyInput, setKeyInput] = useState('');

  // BYOK：sk-byok- 开头的 key 需要调用方自带主对话模型凭据（只存本机）。
  const [byokCreds, setByokCreds] = useState<ByokCreds | null>(() =>
    getStoredByok(config.service_id),
  );
  const [byokModels, setByokModels] = useState<ServiceModelOption[]>([]);
  const [byokForm, setByokForm] = useState({ model: '', api_key: '', base_url: '' });
  const [byokError, setByokError] = useState('');
  const needsByok = !!apiKey && isByokKey(apiKey) && !byokCreds;

  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageEntry[]>([]);
  const [updatesDelayed, setUpdatesDelayed] = useState(false);
  const [historyEpoch, setHistoryEpoch] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const conversationRequest = useRef(0);
  const openingConversation = useRef<string | null>(null);
  const pendingSend = useRef<PendingSend | null>(null);
  const nextLocalMessageId = useRef(0);
  const [creatingMessageId, setCreatingMessageId] = useState<string | null>(null);
  const [sendError, setSendError] = useState('');
  useEffect(() => () => {
    ++conversationRequest.current;
    pendingSend.current = null;
  }, []);
  const [pendingImgs, setPendingImgs] = useState<{ dataUrl: string; name: string }[]>([]);
  const [draft, setDraft] = useState('');
  const [welcomeDismissed, setWelcomeDismissed] = useState(false);
  const [mediaToken, setMediaToken] = useState<string>('');
  const [filesPanelOpen, setFilesPanelOpen] = useState(false);
  const [convList, setConvList] = useState<ConvMeta[]>(() => loadConvStore(config.service_id).items);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 当前视口顶部正在阅读的用户 query 下标（-1 = 无），驱动左侧导航列 active 高亮。
  const [activeQueryIndex, setActiveQueryIndex] = useState(-1);

  // ── 注入 consumer 端的 mediaUrl builder（一次性，依赖 token + convId） ──
  // 用 ref 让闭包总能拿到最新值，但 setMediaUrlBuilder 只调一次。
  const mediaTokenRef = useRef(mediaToken);
  const convIdRef = useRef(conversationId);
  const apiKeyRef = useRef(apiKey);
  useEffect(() => { mediaTokenRef.current = mediaToken; }, [mediaToken]);
  useEffect(() => { convIdRef.current = conversationId; }, [conversationId]);
  useEffect(() => { apiKeyRef.current = apiKey; }, [apiKey]);

  useEffect(() => {
    setMediaUrlBuilder((path) =>
      buildConsumerMediaUrl(mediaTokenRef.current, convIdRef.current, path),
    );
    // service-chat（消费者侧）没有 FilePanel：关闭「在文件浏览器中定位」pill，
    // 改为「直接下载」模式——非媒体 <<FILE:>> 渲染成下载链接，媒体 caption 带下载按钮。
    setFileRevealEnabled(false);
    setFileDownloadMode(true);
  }, []);

  // 媒体 URL builder（供 GeneratedFilesPanel 等使用），随 token / convId 变化
  const buildMediaUrl = useCallback(
    (path: string, opts?: { download?: boolean }) =>
      buildConsumerMediaUrl(mediaToken, conversationId, path, opts),
    [mediaToken, conversationId],
  );

  // ── 会话列表持久化（本浏览器维度，刷新不丢） ──────────────────────
  // 关键：首屏恢复（didInitRef）完成前不写盘，否则会用 conversationId=null
  // 把 localStorage 里的 activeId 清掉，导致刷新无法恢复上次会话。
  const didInitRef = useRef(false);
  useEffect(() => {
    if (!didInitRef.current) return;
    saveConvStore(config.service_id, convList, conversationId);
  }, [convList, conversationId, config.service_id]);

  // ── 取会话级媒体 token（恢复/切换/新建时刷新） ────────────────────
  const refreshMediaToken = useCallback(async (key: string, convId: string) => {
    try {
      const token = await getMediaToken(key, convId);
      if (convIdRef.current === convId && apiKeyRef.current === key) setMediaToken(token);
    } catch (tokErr) {
      console.error('Failed to fetch media token:', tokErr);
      if (convIdRef.current === convId && apiKeyRef.current === key) setMediaToken('');
    }
  }, []);

  // ── 恢复/切换到某个已存在的会话（从后端拉历史消息） ────────────────
  const openConversation = useCallback(
    async (convId: string, key: string) => {
      const sequence = ++conversationRequest.current;
      openingConversation.current = convId;
      setHistoryLoading(true);
      try {
        const conv = await getConversation(key, convId);
        if (sequence !== conversationRequest.current) return;
        if (!conv) {
          // 后端已无此会话（被 admin 删了等）→ 从本地列表清掉
          setConvList((prev) => prev.filter((c) => c.id !== convId));
          if (convIdRef.current === convId) {
            setConversationId(null);
            setMessages([]);
          }
          return;
        }
        const entries = conv.messages
          .map(backendMsgToEntry)
          .filter((e): e is MessageEntry => e !== null);
        setMessages(entries as MessageEntry[]);
        convIdRef.current = convId;
        setConversationId(convId);
        setWelcomeDismissed(true);
        void refreshMediaToken(key, convId);
      } catch (err) {
        if (sequence !== conversationRequest.current) return;
        if (err instanceof AuthError) handleAuthFail(err.message);
        else console.error('Failed to open conversation:', err);
      } finally {
        if (sequence === conversationRequest.current) {
          openingConversation.current = null;
          setHistoryLoading(false);
          // Even a failed switch back to the same ID must restart its event
          // reader. History and event reads share a generation fence.
          setHistoryEpoch((value) => value + 1);
        }
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [refreshMediaToken],
  );

  // ── 首次进入：恢复上次活跃会话（不再每次刷新都新建空会话） ──────────
  useEffect(() => {
    if (!apiKey || didInitRef.current) return;
    const store = loadConvStore(config.service_id);
    setConvList(store.items);
    const activeId = store.activeId;
    // 标记 init 完成 → 之后持久化 effect 才会写盘（避免清掉 activeId）
    didInitRef.current = true;
    if (activeId) void openConversation(activeId, apiKey);
    // React StrictMode remounts effects; allow the cancelled initial read to
    // restart instead of leaving the restored conversation permanently blank.
    return () => { didInitRef.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);

  // ── 懒创建会话（首次发消息时才建，并登记到本地列表） ───────────────
  const createAndRegister = useCallback(
    async (key: string, firstText: string, sequence: number): Promise<string | null> => {
      const conv = await createConversation(key);
      if (sequence !== conversationRequest.current || apiKeyRef.current !== key) return null;
      convIdRef.current = conv.id;
      setConversationId(conv.id);
      const title = makeTitle(firstText);
      setConvList((prev) => [
        { id: conv.id, title, updatedAt: new Date().toISOString() },
        ...prev.filter((c) => c.id !== conv.id),
      ]);
      void refreshMediaToken(key, conv.id);
      return conv.id;
    },
    [refreshMediaToken],
  );

  // A switch during the first create request cannot cancel an already-sent
  // HTTP request. Keep its unsent text in the composer and fence its response.
  const releasePendingSend = useCallback(() => {
    const pending = pendingSend.current;
    pendingSend.current = null;
    setCreatingMessageId(null);
    if (pending?.phase !== 'creating') return false;
    setDraft(pending.text);
    setPendingImgs(pending.images);
    return true;
  }, []);

  // ── Stream handler ──────────────────────────────────────────────
  const stream = useServiceStream({
    onAuthError: () => handleAuthFail(t('service.authError')),
    onError: (msg) => console.error('stream error:', msg),
    onDone: (finalBlocks) => {
      // ❗ finalBlocks 由 hook 直接传入，不能用 stream.blocks（首次 render 闭包永远是 []）
      // 把流式 blocks 固化为一条 assistant message，再清空 hook 内部 state
      if (!finalBlocks || finalBlocks.length === 0) {
        stream.reset();
        return;
      }
      setMessages((prev) => [
        ...prev,
        { kind: 'assistant', data: { blocks: finalBlocks } },
      ]);
      stream.reset();
      // 本轮结束：把当前会话顶到列表最前并更新时间（用于排序展示）
      const id = convIdRef.current;
      if (id) {
        setConvList((prev) => {
          const found = prev.find((c) => c.id === id);
          if (!found) return prev;
          return [
            { ...found, updatedAt: new Date().toISOString() },
            ...prev.filter((c) => c.id !== id),
          ];
        });
      }
    },
  });

  // Durable admin replies/notices are independent of this turn's POST stream.
  // Serial polling + focus recovery also works after a browser was offline.
  useEffect(() => {
    if (!apiKey || !conversationId) return;
    const sequence = conversationRequest.current;
    const controller = new AbortController();
    let stopped = false;
    let busy = false;
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    const valid = () => !stopped && sequence === conversationRequest.current;
    const poll = async () => {
      if (!valid() || busy) return;
      clearTimeout(timer);
      if (document.hidden) { timer = setTimeout(poll, 3000); return; }
      busy = true;
      try {
        const result = await getConversationEvents(apiKey, conversationId, cursor, controller.signal);
        if (!valid()) return;
        setMessages((previous) => {
          if (!valid()) return previous;
          const seen = new Set(previous.map((m) => m.id).filter(Boolean));
          const next = [...previous];
          for (const event of result.events) {
            if (event.conversation_id !== conversationId || seen.has(event.message.id)) continue;
            const entry = backendMsgToEntry({ ...event.message, event_id: event.message.id });
            if (entry) { next.push(entry); seen.add(event.message.id); }
          }
          return next.length === previous.length ? previous : next;
        });
        cursor = result.next_cursor;
        setUpdatesDelayed(false);
        timer = setTimeout(poll, result.has_more ? 0 : 3000);
      } catch (error) {
        if (!valid()) return;
        if (error instanceof AuthError) handleAuthFail(error.message);
        else { setUpdatesDelayed(true); timer = setTimeout(poll, 5000); }
      } finally { busy = false; }
    };
    const focus = () => { void poll(); };
    void poll();
    window.addEventListener('focus', focus);
    document.addEventListener('visibilitychange', focus);
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener('focus', focus);
      document.removeEventListener('visibilitychange', focus);
    };
    // handleAuthFail only uses the current service and stable state setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey, conversationId, historyEpoch]);

  // ── 抽屉操作 ──────────────────────────────────────────────────────
  const handleSelectConversation = useCallback(
    (convId: string) => {
      setDrawerOpen(false);
      if (convId === convIdRef.current && !openingConversation.current) return;
      releasePendingSend();
      setSendError('');
      stream.abort();
      stream.reset();
      void openConversation(convId, apiKeyRef.current);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [openConversation, releasePendingSend, stream],
  );

  const handleNewConversation = useCallback(() => {
    const recoveredDraft = releasePendingSend();
    ++conversationRequest.current;
    openingConversation.current = null;
    setHistoryLoading(false);
    convIdRef.current = null;
    setDrawerOpen(false);
    stream.abort();
    stream.reset();
    setConversationId(null);
    setMessages([]);
    setMediaToken('');
    if (!recoveredDraft) {
      setDraft('');
      setPendingImgs([]);
    }
    setSendError('');
    setWelcomeDismissed(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [releasePendingSend, stream]);

  const handleDeleteConversation = useCallback(
    (convId: string) => {
      // 仅本地移除（服务器数据保留）
      setConvList((prev) => prev.filter((c) => c.id !== convId));
      if (convIdRef.current === convId) {
        releasePendingSend();
        stream.abort();
        stream.reset();
        ++conversationRequest.current;
        openingConversation.current = null;
        setHistoryLoading(false);
        convIdRef.current = null;
        setConversationId(null);
        setMessages([]);
        setMediaToken('');
        setSendError('');
        setWelcomeDismissed(false);
      } else if (openingConversation.current === convId) {
        ++conversationRequest.current;
        openingConversation.current = null;
        setHistoryLoading(false);
        setHistoryEpoch((value) => value + 1);
      }
    },
    [releasePendingSend, stream],
  );

  function handleAuthFail(msg: string) {
    releasePendingSend();
    stream.abort();
    stream.reset();
    ++conversationRequest.current;
    openingConversation.current = null;
    setHistoryLoading(false);
    convIdRef.current = null;
    setApiKey('');
    clearStoredKey(config.service_id);
    setAuthError(msg);
    setConversationId(null);
    setMessages([]);
    setMediaToken('');
    setFilesPanelOpen(false);
    setDrawerOpen(false);
    setSendError('');
    // 允许重新登录后再次恢复活跃会话（会话列表本身保留在 localStorage）
    didInitRef.current = false;
  }

  // ── BYOK：拉取本 Key 允许的模型，供凭据表单选择 ────────────────────
  useEffect(() => {
    if (!needsByok) return;
    let cancelled = false;
    void (async () => {
      try {
        const info = await getServiceModels(apiKey);
        if (cancelled) return;
        setByokModels(info.models);
        setByokForm((f) => ({
          ...f,
          model: f.model || info.default_model || info.models[0]?.id || '',
        }));
      } catch (err) {
        if (!cancelled) setByokError((err as Error).message);
      }
    })();
    return () => { cancelled = true; };
  }, [needsByok, apiKey]);

  function handleByokSubmit() {
    const model = byokForm.model.trim();
    const key = byokForm.api_key.trim();
    if (!model || !key) {
      setByokError(t('service.byokIncomplete', '请选择模型并填写 API Key'));
      return;
    }
    const provider = model.split(':', 1)[0];
    const creds: ByokCreds = { provider, model, api_key: key };
    const base = byokForm.base_url.trim();
    if (base) creds.base_url = base;
    setStoredByok(config.service_id, creds);
    setByokCreds(creds);
    setByokError('');
  }

  function handleAuthSubmit() {
    const key = keyInput.trim();
    if (!key) {
      setAuthError(t('service.authEmpty'));
      return;
    }
    setStoredKey(config.service_id, key);
    setApiKey(key);
    setAuthError('');
    setKeyInput('');
  }

  // ── 自动滚动 ────────────────────────────────────────────────────
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, stream.blocks]);

  // ── textarea 自动高度 ────────────────────────────────────────────
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  }, [draft]);

  // ── 图片附件 ────────────────────────────────────────────────────
  function addImageFiles(files: FileList | File[]) {
    const arr = Array.from(files).filter((f) => f.type.startsWith('image/'));
    setPendingImgs((prev) => {
      const remain = MAX_PENDING_IMAGES - prev.length;
      const slice = arr.slice(0, Math.max(0, remain));
      const promises = slice.map(
        (f) =>
          new Promise<{ dataUrl: string; name: string }>((resolve) => {
            const r = new FileReader();
            r.onload = () => resolve({ dataUrl: r.result as string, name: f.name });
            r.readAsDataURL(f);
          }),
      );
      Promise.all(promises).then((loaded) => {
        if (loaded.length === 0) return;
        setPendingImgs((cur) => [...cur, ...loaded].slice(0, MAX_PENDING_IMAGES));
      });
      return prev;
    });
  }

  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const items = e.clipboardData.items;
    const imgs: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.type.startsWith('image/')) {
        const f = it.getAsFile();
        if (f) imgs.push(f);
      }
    }
    if (imgs.length) {
      e.preventDefault();
      addImageFiles(imgs);
    }
  }

  // ── 发送消息 ────────────────────────────────────────────────────
  const handleSend = useCallback(
    async (overrideText?: string) => {
      if (stream.isStreaming || openingConversation.current || historyLoading || pendingSend.current) return;
      const text = (overrideText ?? draft).trim();
      const imgs = pendingImgs.slice();
      if (!text && imgs.length === 0) return;

      let payload: string | unknown[];
      if (imgs.length > 0) {
        const arr: unknown[] = [];
        if (text) arr.push({ type: 'text', text });
        for (const img of imgs) {
          arr.push({ type: 'image_url', image_url: { url: img.dataUrl } });
        }
        payload = arr;
      } else {
        payload = text;
      }

      const submission: PendingSend = {
        id: `local-${++nextLocalMessageId.current}`,
        text,
        images: imgs,
        phase: convIdRef.current ? 'streaming' : 'creating',
      };
      // Set the ref before the first await so a second click cannot submit
      // the same draft while React is still batching this render.
      pendingSend.current = submission;
      setSendError('');
      setMessages((prev) => [
        ...prev,
        { kind: 'user', id: submission.id,
          data: { text: text || '[image]', images: imgs.map((i) => i.dataUrl) } },
      ]);
      setDraft('');
      setPendingImgs([]);
      setWelcomeDismissed(true);

      try {
        let convId = convIdRef.current;
        if (!convId) {
          const sequence = ++conversationRequest.current;
          setCreatingMessageId(submission.id);
          convId = await createAndRegister(apiKey, text, sequence);
          if (!convId || pendingSend.current !== submission) return;
          submission.phase = 'streaming';
          setCreatingMessageId(null);
        }
        if (pendingSend.current !== submission || apiKeyRef.current !== apiKey) return;
        await stream.send(apiKey, {
          conversation_id: convId,
          message: payload,
          ...(byokCreds ?? {}),
        });
      } catch (err) {
        if (pendingSend.current !== submission) return;
        if (submission.phase === 'creating') {
          // Creation failed before the message could be sent. Roll back its
          // bubble and restore the exact text/images for an ordinary retry.
          setMessages((prev) => prev.filter((m) => m.id !== submission.id));
          releasePendingSend();
          setWelcomeDismissed(false);
          if (err instanceof AuthError) handleAuthFail(err.message);
          else setSendError(t('service.createConvFail', {
            status: err instanceof Error ? err.message : String(err),
          }));
        } else {
          setSendError(t('service.networkError', {
            msg: err instanceof Error ? err.message : String(err),
          }));
        }
      } finally {
        if (pendingSend.current === submission) {
          pendingSend.current = null;
          setCreatingMessageId(null);
        }
      }
    },
    [apiKey, byokCreds, draft, pendingImgs, stream, createAndRegister, historyLoading, releasePendingSend, t],
  );

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  // ── 欢迎屏 / 快速问题 ────────────────────────────────────────────
  const showWelcome = useMemo(() => {
    if (welcomeDismissed) return false;
    if (messages.length > 0 || stream.isStreaming) return false;
    const hasMsg = !!(config.welcome_message && config.welcome_message.trim());
    const hasQs = Array.isArray(config.quick_questions) && config.quick_questions.length > 0;
    return hasMsg || hasQs;
  }, [welcomeDismissed, messages.length, stream.isStreaming, config]);

  const showEmpty = !showWelcome && messages.length === 0 && !stream.isStreaming;

  // ── 左侧固定导航列：每条用户 query 一根短横，滚动联动高亮 ──────────────
  const historyMarkers = useMemo(() => {
    const items: { id: string; question: string; answer: string }[] = [];
    messages.forEach((message, index) => {
      if (message.kind === 'user') items.push({ id: String(index), question: plainPreview(message.data.text), answer: '' });
      else if (items.length) items[items.length - 1].answer = answerPreview(message.data.blocks);
    });
    return items;
  }, [messages]);
  const userMarkers = useMemo(() => {
    if (!stream.blocks.length || !historyMarkers.length) return historyMarkers;
    return [...historyMarkers.slice(0, -1), { ...historyMarkers[historyMarkers.length - 1], answer: answerPreview(stream.blocks) }];
  }, [historyMarkers, stream.blocks]);

  // 点击导航 → 平滑滚动到对应用户消息（service 端不虚拟化，DOM 节点恒在）。
  const scrollToMessage = useCallback((index: string) => {
    const node = messagesContainerRef.current?.querySelector<HTMLElement>(
      `[data-jf-msg-index="${index}"]`,
    );
    node?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }, []);

  // 滚动联动 active：取「容器顶 +80px 基准线之上最靠近」的用户消息（DOM rect 法）。
  useEffect(() => {
    const el = messagesContainerRef.current;
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
      const lineY = el.getBoundingClientRect().top + 80;
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
  }, [messages, showWelcome, stream.blocks]);

  // ── 渲染 ────────────────────────────────────────────────────────
  // Header right slot: language switcher (no backend sync — consumers can't
  // hit /api/preferences without an admin token).
  const headerLangSwitcher = (
    <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center' }}>
      <LanguageSwitcher variant="icon" placement="bottom" syncBackend={false} />
    </div>
  );

  if (!apiKey) {
    return (
      <div className={styles.page}>
        <header className={styles.header}>
          <div className={styles.headerLogo}>S</div>
          <h1 className={styles.headerTitle}>{config.service_name}</h1>
          {config.service_desc && (
            <span className={styles.headerDesc}>{config.service_desc}</span>
          )}
          {headerLangSwitcher}
        </header>
        <div className={styles.authOverlay}>
          <div className={styles.authBox}>
            <h2>{config.service_name}</h2>
            <p>{config.service_desc || ''}</p>
            <input
              type="text"
              autoComplete="off"
              placeholder={t('service.authPlaceholder')}
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleAuthSubmit();
                }
              }}
              autoFocus
            />
            {authError && <div className={styles.authError}>{authError}</div>}
            <button type="button" onClick={handleAuthSubmit}>
              {t('service.authStart')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (needsByok) {
    return (
      <div className={styles.page}>
        <header className={styles.header}>
          <div className={styles.headerLogo}>S</div>
          <h1 className={styles.headerTitle}>{config.service_name}</h1>
          {headerLangSwitcher}
        </header>
        <div className={styles.authOverlay}>
          <div className={styles.authBox}>
            <h2>{t('service.byokTitle', '填写你的模型凭据')}</h2>
            <p>
              {t(
                'service.byokHint',
                '该 Key 的对话由你自己的模型账号付费。凭据只保存在本浏览器，不会上传保存。',
              )}
            </p>
            <select
              value={byokForm.model}
              onChange={(e) => setByokForm((f) => ({ ...f, model: e.target.value }))}
            >
              <option value="">{t('service.byokModelPlaceholder', '选择模型')}</option>
              {byokModels.map((m) => (
                <option key={m.id} value={m.id}>{m.display_name}</option>
              ))}
            </select>
            <input
              type="password"
              autoComplete="off"
              placeholder={t('service.byokKeyPlaceholder', '你的模型 API Key')}
              value={byokForm.api_key}
              onChange={(e) => setByokForm((f) => ({ ...f, api_key: e.target.value }))}
            />
            <input
              type="text"
              autoComplete="off"
              placeholder={t('service.byokBaseUrlPlaceholder', 'Base URL（可留空用默认）')}
              value={byokForm.base_url}
              onChange={(e) => setByokForm((f) => ({ ...f, base_url: e.target.value }))}
            />
            {byokError && <div className={styles.authError}>{byokError}</div>}
            <button type="button" onClick={handleByokSubmit}>
              {t('service.authStart')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <button
          type="button"
          className={styles.menuBtn}
          onClick={() => setDrawerOpen(true)}
          title={t('service.convMenu', '会话')}
          aria-label={t('service.convMenu', '会话')}
        >
          ☰
        </button>
        <div className={styles.headerLogo}>S</div>
        <h1 className={styles.headerTitle}>{config.service_name}</h1>
        {config.service_desc && (
          <span className={styles.headerDesc}>{config.service_desc}</span>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          {conversationId && (
            <button
              type="button"
              className={styles.filesBtn}
              onClick={() => setFilesPanelOpen(true)}
              title={t('service.filesTitle', '本会话生成文件')}
            >
              📁 {t('service.filesBtn', '文件')}
            </button>
          )}
          <LanguageSwitcher variant="icon" placement="bottom" syncBackend={false} />
        </div>
      </header>

      {drawerOpen && (
        <ConversationDrawer
          items={convList}
          activeId={conversationId}
          onSelect={handleSelectConversation}
          onNew={handleNewConversation}
          onDelete={handleDeleteConversation}
          onClose={() => setDrawerOpen(false)}
        />
      )}

      {filesPanelOpen && conversationId && (
        <GeneratedFilesPanel
          apiKey={apiKey}
          convId={conversationId}
          buildUrl={buildMediaUrl}
          onClose={() => setFilesPanelOpen(false)}
        />
      )}

      {/* 左侧 query 快速导航：悬浮在 .page 左侧垂直居中，脱离滚动容器(.messages)，
          不随消息滚动消失；bar 数 = q 数，active 高亮，点击跳转。 */}
      {!showWelcome && userMarkers.length > 0 && (
        <QueryNavigation items={userMarkers} activeId={String(activeQueryIndex)} onJump={scrollToMessage} />
      )}

      {showWelcome ? (
        <div className={styles.welcomeScreen}>
          <div className={styles.welcomeTitle}>{config.service_name || t('service.welcomeFallback')}</div>
          {config.welcome_message && (
            <div className={styles.welcomeMessage}>{config.welcome_message}</div>
          )}
          {config.quick_questions && config.quick_questions.length > 0 && (
            <div className={styles.quickQuestions}>
              {config.quick_questions
                .filter((q) => typeof q === 'string' && q.trim())
                .map((q, i) => (
                  <button
                    key={i}
                    type="button"
                    className={styles.quickChip}
                    title={q}
                    disabled={stream.isStreaming || creatingMessageId !== null}
                    onClick={() => void handleSend(q)}
                  >
                    {q}
                  </button>
                ))}
            </div>
          )}
        </div>
      ) : (
        <div className={styles.messages} ref={messagesContainerRef}>
          {updatesDelayed && <p role="status">{t('serviceMessaging.updatesDelayed')}</p>}
          {showEmpty && (
            <div className={styles.emptyState}>
              <div className={styles.emptyStateIcon}>💬</div>
              <p>{t('service.emptyHint')}</p>
            </div>
          )}
          {messages.map((m, i) =>
            m.kind === 'user' ? (
              <div key={`u-${i}`} className={styles.userMsg} data-jf-msg-index={i} data-jf-msg-role="user">
                {m.data.text}
                {m.data.images.length > 0 && (
                  <div className={styles.userMsgImages}>
                    {m.data.images.map((src, j) => (
                      <img
                        key={j}
                        src={src}
                        alt=""
                        onClick={() => window.open(src, '_blank')}
                      />
                    ))}
                  </div>
                )}
                {m.id === creatingMessageId && (
                  <div className={styles.userMsgStatus} role="status">{t('service.sending')}</div>
                )}
              </div>
            ) : (
              <div key={m.id || `a-${i}`}>
              {m.authorType === 'admin' && <div className={styles.adminMessageLabel}>{t('serviceMessaging.adminReply')}</div>}
              <StreamingMessage
                blocks={m.data.blocks}
                isStreaming={false}
                toolRenderer={ServiceToolBadge}
                hideSubagents
                scheduledTaskFriendlyMode
              />
              </div>
            ),
          )}
          {(stream.isStreaming || stream.blocks.length > 0) && (
            // 防御性：只要还有未提交的流式 blocks 就继续渲染，即便 isStreaming 已翻 false
            // （中断/异常路径），避免已生成内容在提交前从视图消失。正常/中断结束都会
            // 走 onDone 把 blocks 固化进 messages 并 reset，届时 stream.blocks 清空不再重复。
            <StreamingMessage
              blocks={stream.blocks}
              isStreaming={stream.isStreaming}
              toolRenderer={ServiceToolBadge}
              hideSubagents
              scheduledTaskFriendlyMode
            />
          )}
          <div ref={messagesEndRef} />
        </div>
      )}

      <div className={styles.inputArea}>
        {sendError && <p className={styles.sendError} role="alert">{sendError}</p>}
        {pendingImgs.length > 0 && (
          <div className={styles.imgPreview}>
            {pendingImgs.map((img, i) => (
              <div key={i} className={styles.imgThumb}>
                <img src={img.dataUrl} alt={img.name} />
                <button
                  type="button"
                  className={styles.imgThumbRm}
                  onClick={() => setPendingImgs((prev) => prev.filter((_, j) => j !== i))}
                  aria-label={t('service.removeImage')}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <form
          className={styles.inputForm}
          onSubmit={(e) => {
            e.preventDefault();
            void handleSend();
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            style={{ display: 'none' }}
            onChange={(e) => {
              if (e.target.files) addImageFiles(e.target.files);
              e.target.value = '';
            }}
          />
          <button
            type="button"
            className={styles.btnAttach}
            title={t('service.uploadImage')}
            onClick={() => fileInputRef.current?.click()}
            disabled={historyLoading || stream.isStreaming || creatingMessageId !== null || pendingImgs.length >= MAX_PENDING_IMAGES}
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <circle cx="8.5" cy="8.5" r="1.5" />
              <polyline points="21 15 16 10 5 21" />
            </svg>
          </button>
          <textarea
            ref={textareaRef}
            className={styles.inputTextarea}
            placeholder={t('service.inputPlaceholder')}
            value={draft}
            rows={1}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            disabled={historyLoading || stream.isStreaming || creatingMessageId !== null}
          />
          <button
            type="submit"
            className={styles.btnSend}
            disabled={
              historyLoading || stream.isStreaming || creatingMessageId !== null ||
              (!draft.trim() && pendingImgs.length === 0)
            }
          >
            {t('service.sendBtn')}
          </button>
        </form>
      </div>
    </div>
  );
}

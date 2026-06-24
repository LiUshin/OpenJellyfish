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
import QueryNavMarker from '../pages/Chat/components/QueryNavMarker';
import { setMediaUrlBuilder, setFileRevealEnabled, setFileDownloadMode } from '../pages/Chat/markdown';
import {
  AuthError,
  buildConsumerMediaUrl,
  clearStoredKey,
  consumeKeyFromUrl,
  createConversation,
  getConversation,
  getMediaToken,
  getStoredKey,
  loadConvStore,
  saveConvStore,
  setStoredKey,
  type ConsumerMessage,
  type ConvMeta,
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

type MessageEntry =
  | { kind: 'user'; data: UserMessage }
  | { kind: 'assistant'; data: AssistantMessage };

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
    return { kind: 'assistant', data: { blocks } };
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

  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageEntry[]>([]);
  const [pendingImgs, setPendingImgs] = useState<{ dataUrl: string; name: string }[]>([]);
  const [draft, setDraft] = useState('');
  const [welcomeDismissed, setWelcomeDismissed] = useState(false);
  const [mediaToken, setMediaToken] = useState<string>('');
  const [filesPanelOpen, setFilesPanelOpen] = useState(false);
  const [convList, setConvList] = useState<ConvMeta[]>(() => loadConvStore(config.service_id).items);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLElement | null>(null);
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
      setMediaToken(await getMediaToken(key, convId));
    } catch (tokErr) {
      console.error('Failed to fetch media token:', tokErr);
      setMediaToken('');
    }
  }, []);

  // ── 恢复/切换到某个已存在的会话（从后端拉历史消息） ────────────────
  const openConversation = useCallback(
    async (convId: string, key: string) => {
      try {
        const conv = await getConversation(key, convId);
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
        setConversationId(convId);
        setWelcomeDismissed(true);
        void refreshMediaToken(key, convId);
      } catch (err) {
        if (err instanceof AuthError) handleAuthFail(err.message);
        else console.error('Failed to open conversation:', err);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);

  // ── 懒创建会话（首次发消息时才建，并登记到本地列表） ───────────────
  const createAndRegister = useCallback(
    async (key: string, firstText: string): Promise<string | null> => {
      try {
        const conv = await createConversation(key);
        setConversationId(conv.id);
        const title = makeTitle(firstText);
        setConvList((prev) => [
          { id: conv.id, title, updatedAt: new Date().toISOString() },
          ...prev.filter((c) => c.id !== conv.id),
        ]);
        void refreshMediaToken(key, conv.id);
        return conv.id;
      } catch (err) {
        if (err instanceof AuthError) handleAuthFail(err.message);
        else console.error('Failed to create conversation:', err);
        return null;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [refreshMediaToken],
  );

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

  // ── 抽屉操作 ──────────────────────────────────────────────────────
  const handleSelectConversation = useCallback(
    (convId: string) => {
      setDrawerOpen(false);
      if (convId === convIdRef.current) return;
      stream.abort();
      stream.reset();
      void openConversation(convId, apiKeyRef.current);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [openConversation, stream],
  );

  const handleNewConversation = useCallback(() => {
    setDrawerOpen(false);
    stream.abort();
    stream.reset();
    setConversationId(null);
    setMessages([]);
    setMediaToken('');
    setDraft('');
    setPendingImgs([]);
    setWelcomeDismissed(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream]);

  const handleDeleteConversation = useCallback(
    (convId: string) => {
      // 仅本地移除（服务器数据保留）
      setConvList((prev) => prev.filter((c) => c.id !== convId));
      if (convIdRef.current === convId) {
        setConversationId(null);
        setMessages([]);
        setMediaToken('');
        setWelcomeDismissed(false);
      }
    },
    [],
  );

  function handleAuthFail(msg: string) {
    setApiKey('');
    clearStoredKey(config.service_id);
    setAuthError(msg);
    setConversationId(null);
    setMediaToken('');
    setFilesPanelOpen(false);
    setDrawerOpen(false);
    // 允许重新登录后再次恢复活跃会话（会话列表本身保留在 localStorage）
    didInitRef.current = false;
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
      if (stream.isStreaming) return;
      const text = (overrideText ?? draft).trim();
      const imgs = pendingImgs.slice();
      if (!text && imgs.length === 0) return;

      let convId = conversationId;
      if (!convId) {
        convId = await createAndRegister(apiKey, text);
        if (!convId) return;
      }

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

      // 立即把用户消息推入 list
      setMessages((prev) => [
        ...prev,
        { kind: 'user', data: { text: text || '[image]', images: imgs.map((i) => i.dataUrl) } },
      ]);
      setDraft('');
      setPendingImgs([]);
      setWelcomeDismissed(true);

      void stream.send(apiKey, { conversation_id: convId, message: payload });
    },
    [apiKey, conversationId, draft, pendingImgs, stream, createAndRegister],
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
  const userMarkers = useMemo(
    () =>
      messages
        .map((m, index) => ({ index, kind: m.kind, text: m.kind === 'user' ? m.data.text : '' }))
        .filter((x) => x.kind === 'user'),
    [messages],
  );

  // 点击导航 → 平滑滚动到对应用户消息（service 端不虚拟化，DOM 节点恒在）。
  const scrollToMessage = useCallback((index: number) => {
    const node = messagesContainerRef.current?.querySelector<HTMLElement>(
      `[data-jf-msg-index="${index}"]`,
    );
    node?.scrollIntoView({ block: 'start', behavior: 'smooth' });
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

  // active 变化时把对应标记滚入导航列可视区——仅当导航列自身溢出时手动滚 scrollTop，
  // 否则 scrollIntoView 会冒泡去滚动消息容器，把用户「拉回」（旧 bug 根因）。
  useEffect(() => {
    if (activeQueryIndex < 0) return;
    const rail = railRef.current;
    if (!rail) return;
    if (rail.scrollHeight <= rail.clientHeight + 1) return;
    const node = rail.querySelector<HTMLElement>(`[data-jf-nav-index="${activeQueryIndex}"]`);
    if (!node) return;
    const rTop = rail.scrollTop;
    const rBottom = rTop + rail.clientHeight;
    const nTop = node.offsetTop;
    const nBottom = nTop + node.offsetHeight;
    if (nTop < rTop) rail.scrollTop = nTop - 8;
    else if (nBottom > rBottom) rail.scrollTop = nBottom - rail.clientHeight + 8;
  }, [activeQueryIndex]);

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
        <nav ref={railRef} className={styles.queryNavRail} aria-label="Jump to your messages">
          {userMarkers.map((m) => (
            <span
              key={m.index}
              data-jf-nav-index={m.index}
              className={styles.queryNavRailItem}
            >
              <QueryNavMarker
                preview={m.text}
                active={m.index === activeQueryIndex}
                onClick={() => scrollToMessage(m.index)}
              />
            </span>
          ))}
        </nav>
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
                    disabled={stream.isStreaming}
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
              </div>
            ) : (
              <StreamingMessage
                key={`a-${i}`}
                blocks={m.data.blocks}
                isStreaming={false}
                toolRenderer={ServiceToolBadge}
                hideSubagents
                scheduledTaskFriendlyMode
              />
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
            disabled={stream.isStreaming || pendingImgs.length >= MAX_PENDING_IMAGES}
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
            disabled={stream.isStreaming}
          />
          <button
            type="submit"
            className={styles.btnSend}
            disabled={
              stream.isStreaming ||
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

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
import StreamingMessage from '../pages/Chat/components/StreamingMessage';
import { setMediaUrlBuilder } from '../pages/Chat/markdown';
import {
  AuthError,
  buildConsumerMediaUrl,
  clearStoredKey,
  consumeKeyFromUrl,
  createConversation,
  getStoredKey,
  setStoredKey,
} from './serviceApi';
import { useServiceStream } from './streamHandler';
import ServiceToolBadge from './ServiceToolBadge';
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

export default function ServiceChatApp({ config }: { config: ServiceConfig }) {
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

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // ── 注入 consumer 端的 mediaUrl builder（一次性，依赖 apiKey + convId） ──
  // 用 ref 让闭包总能拿到最新值，但 setMediaUrlBuilder 只调一次。
  const apiKeyRef = useRef(apiKey);
  const convIdRef = useRef(conversationId);
  useEffect(() => { apiKeyRef.current = apiKey; }, [apiKey]);
  useEffect(() => { convIdRef.current = conversationId; }, [conversationId]);

  useEffect(() => {
    setMediaUrlBuilder((path) =>
      buildConsumerMediaUrl(apiKeyRef.current, convIdRef.current, path),
    );
  }, []);

  // ── 创建/恢复会话 ─────────────────────────────────────────────────
  const initConversation = useCallback(
    async (key: string): Promise<string | null> => {
      try {
        const conv = await createConversation(key);
        setConversationId(conv.id);
        return conv.id;
      } catch (err) {
        if (err instanceof AuthError) {
          handleAuthFail(err.message);
        } else {
          console.error('Failed to create conversation:', err);
        }
        return null;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  useEffect(() => {
    if (apiKey && !conversationId) {
      void initConversation(apiKey);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);

  // ── Stream handler ──────────────────────────────────────────────
  const stream = useServiceStream({
    onAuthError: () => handleAuthFail('API Key 无效，请重新输入'),
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
    },
  });

  function handleAuthFail(msg: string) {
    setApiKey('');
    clearStoredKey(config.service_id);
    setAuthError(msg);
    setConversationId(null);
  }

  function handleAuthSubmit() {
    const key = keyInput.trim();
    if (!key) {
      setAuthError('请输入 API Key');
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
        convId = await initConversation(apiKey);
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
        { kind: 'user', data: { text: text || '[图片]', images: imgs.map((i) => i.dataUrl) } },
      ]);
      setDraft('');
      setPendingImgs([]);
      setWelcomeDismissed(true);

      void stream.send(apiKey, { conversation_id: convId, message: payload });
    },
    [apiKey, conversationId, draft, pendingImgs, stream, initConversation],
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

  // ── 渲染 ────────────────────────────────────────────────────────
  if (!apiKey) {
    return (
      <div className={styles.page}>
        <header className={styles.header}>
          <div className={styles.headerLogo}>S</div>
          <h1 className={styles.headerTitle}>{config.service_name}</h1>
          {config.service_desc && (
            <span className={styles.headerDesc}>{config.service_desc}</span>
          )}
        </header>
        <div className={styles.authOverlay}>
          <div className={styles.authBox}>
            <h2>{config.service_name}</h2>
            <p>{config.service_desc || ''}</p>
            <input
              type="text"
              autoComplete="off"
              placeholder="请输入 API Key (sk-svc-...)"
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
              开始对话
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerLogo}>S</div>
        <h1 className={styles.headerTitle}>{config.service_name}</h1>
        {config.service_desc && (
          <span className={styles.headerDesc}>{config.service_desc}</span>
        )}
      </header>

      {showWelcome ? (
        <div className={styles.welcomeScreen}>
          <div className={styles.welcomeTitle}>{config.service_name || '你好'}</div>
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
        <div className={styles.messages}>
          {showEmpty && (
            <div className={styles.emptyState}>
              <div className={styles.emptyStateIcon}>💬</div>
              <p>发送消息开始对话</p>
            </div>
          )}
          {messages.map((m, i) =>
            m.kind === 'user' ? (
              <div key={`u-${i}`} className={styles.userMsg}>
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
              />
            ),
          )}
          {stream.isStreaming && (
            <StreamingMessage
              blocks={stream.blocks}
              isStreaming
              toolRenderer={ServiceToolBadge}
              hideSubagents
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
                  aria-label="移除图片"
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
            title="上传图片"
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
            placeholder="输入消息... (可粘贴图片)"
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
            发送
          </button>
        </form>
      </div>
    </div>
  );
}

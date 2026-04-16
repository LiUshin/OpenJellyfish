import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Button, Input, Select, Tooltip, App } from 'antd';
import {
  Plus,
  PaperPlaneRight,
  Trash,
  Globe,
  Palette,
  SpeakerHigh,
  VideoCamera,
  CaretDown,
  ListChecks,
  Stop,
  Paperclip,
} from '@phosphor-icons/react';
import * as api from '../../services/api';
import type { Conversation, Message } from '../../types';
import MessageBubble from './components/MessageBubble';
import StreamingMessage from './components/StreamingMessage';
import ApprovalCard from './components/ApprovalCard';
import PlanTracker, { PlanCompactBar } from './components/PlanTracker';
import ImageAttachment from './components/ImageAttachment';
import type { ImageAttachmentHandle } from './components/ImageAttachment';
import VoiceInput from './components/VoiceInput';
import { useSmartScroll } from './useSmartScroll';
import { useStream } from '../../stores/streamContext';
import LogoLoading from '../../components/LogoLoading';
import HeaderControls from '../../components/HeaderControls';
import { useFileWorkspace } from '../../stores/fileWorkspaceContext';
import styles from './chat.module.css';

const { TextArea } = Input;

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

const CAPABILITIES = [
  { key: 'web', label: '联网', icon: <Globe size={16} /> },
  { key: 'image', label: '绘图', icon: <Palette size={16} /> },
  { key: 'speech', label: '语音', icon: <SpeakerHigh size={16} /> },
  { key: 'video', label: '视频', icon: <VideoCamera size={16} /> },
];

const SUGGESTIONS = [
  { emoji: '🛠️', text: '你能做什么', msg: '你可以帮我做哪些事情？请列举你的主要功能和使用场景' },
  { emoji: '⏰', text: '如何设置定时任务', msg: '如何设置定时任务？请帮我介绍定时任务的配置方式和使用方法' },
  { emoji: '🤖', text: '管理 Subagent', msg: '如何创建和管理 Subagent？请介绍 Subagent 的用途和配置方法' },
  { emoji: '📡', text: '分发 Service', msg: '如何创建和分发 Service？请介绍 Service 的发布和管理流程' },
];

export default function ChatPage() {
  const { message: messageApi } = App.useApp();
  const { editingFile, splitMode, setSplitMode } = useFileWorkspace();
  const stream = useStream();
  const {
    streamingConvId, isStreaming, streamBlocks, interruptData, planSteps,
    startStream, resumeStream, stopStream, clearFinished, restoreInterrupt,
  } = stream;

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentConvId, setCurrentConvId] = useState<string | null>(null);
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

  const imageAttachRef = useRef<ImageAttachmentHandle>(null);
  const currentConvIdRef = useRef(currentConvId);
  currentConvIdRef.current = currentConvId;

  const isViewingStream = currentConvId === streamingConvId;
  const showStreamBlocks = isViewingStream && (isStreaming || streamBlocks.length > 0);

  const { containerRef, scrollToBottom, resetScroll, isScrolledUp } = useSmartScroll(
    isViewingStream && isStreaming,
  );

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
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (e: unknown) {
      messageApi.error(e instanceof Error ? e.message : '加载对话失败');
    }
  }

  async function loadModels() {
    try {
      const data = await api.getModels();
      setModels(data.models);
      setSelectedModel(data.default);
    } catch { /* ignore */ }
  }

  const loadMessagesRef = useRef(0);
  const splitRef = useRef(splitMode);
  splitRef.current = splitMode;
  const editingRef = useRef(editingFile);
  editingRef.current = editingFile;

  const loadMessages = useCallback(async (convId: string) => {
    if (splitRef.current === 'file' && editingRef.current) setSplitMode('split');
    const seq = ++loadMessagesRef.current;
    setCurrentConvId(convId);
    setMessages([]);
    setLoadingConv(true);
    try {
      const detail = await api.getConversation(convId);
      if (seq !== loadMessagesRef.current) return;
      setMessages(detail.messages || []);
      if (convId !== streamingConvId) {
        requestAnimationFrame(() => resetScroll());
      }
    } catch (e: unknown) {
      if (seq !== loadMessagesRef.current) return;
      messageApi.error(e instanceof Error ? e.message : '加载消息失败');
    } finally {
      if (seq === loadMessagesRef.current) setLoadingConv(false);
    }
  }, [messageApi, resetScroll, streamingConvId, setSplitMode]);

  async function handleNewChat() {
    try {
      const conv = await api.createConversation();
      setConversations((prev) => [conv, ...prev]);
      setCurrentConvId(conv.id);
      setMessages([]);
    } catch (e: unknown) {
      messageApi.error(e instanceof Error ? e.message : '创建对话失败');
    }
  }

  async function handleDeleteConv(convId: string) {
    if (convId === streamingConvId && isStreaming) {
      await stopStream();
    }
    try {
      await api.deleteConversation(convId);
      setConversations((prev) => prev.filter((c) => c.id !== convId));
      if (currentConvId === convId) {
        setCurrentConvId(null);
        setMessages([]);
      }
    } catch (e: unknown) {
      messageApi.error(e instanceof Error ? e.message : '删除失败');
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
    }).catch(() => {});
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
    resumeStream(currentConvId, decisions, {
      model: selectedModel,
      capabilities,
      onDone: handleStreamDone,
      onError: handleStreamError,
    });
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
      messageApi.success('已请求终止上一轮对话');
      setTimeout(async () => {
        await checkServerStreaming();
        const detail = await api.getConversation(convId);
        if (convId === currentConvIdRef.current) {
          setMessages(detail.messages || []);
        }
        loadConversations();
      }, 1000);
    } catch {
      messageApi.error('终止请求失败');
    }
  }

  function navigateToStreamingConv() {
    if (streamingConvId) loadMessages(streamingConvId);
  }

  async function handleSend(text?: string) {
    const msg = text ?? inputValue.trim();
    const hasImages = attachedImages.length > 0;
    if (!msg && !hasImages) return;
    if (isStreaming || interruptData) {
      const streamTitle = conversations.find((c) => c.id === streamingConvId)?.title || '对话';
      messageApi.warning({
        content: `「${streamTitle}」正在运行中，请等待完成或手动停止后再发送`,
        duration: 3,
      });
      return;
    }
    if (serverStreaming.includes(currentConvId ?? '')) {
      messageApi.warning({
        content: '当前对话的上一轮仍在后台运行中，请先终止后再发送',
        duration: 3,
      });
      return;
    }
    if (serverInterrupted.includes(currentConvId ?? '')) {
      messageApi.warning({
        content: '当前对话有待审批的操作，请先处理审批或终止后再发送',
        duration: 3,
      });
      return;
    }

    let convId = currentConvId;
    if (!convId) {
      try {
        const title = msg ? msg.slice(0, 30) : '图片对话';
        const conv = await api.createConversation(title);
        setConversations((prev) => [conv, ...prev]);
        convId = conv.id;
        setCurrentConvId(convId);
      } catch {
        messageApi.error('创建对话失败');
        return;
      }
    }

    const displayContent = hasImages
      ? (msg || '') + attachedImages.map((img) => `\n![${img.name}](${img.dataUrl})`).join('')
      : msg;
    const userMessage: Message = { role: 'user', content: displayContent };
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
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

    startStream(convId, messageContent, {
      model: selectedModel,
      capabilities,
      plan_mode: planMode || undefined,
      onDone: handleStreamDone,
      onError: handleStreamError,
    });
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handlePaste(e: React.ClipboardEvent) {
    if (isStreaming) return;
    const items = Array.from(e.clipboardData.items);
    const imageFiles = items
      .filter((item) => item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter(Boolean) as File[];
    if (imageFiles.length > 0) {
      e.preventDefault();
      const MAX_IMAGES = 5;
      const remaining = MAX_IMAGES - attachedImages.length;
      if (remaining <= 0) return;
      const toProcess = imageFiles.slice(0, remaining);
      Promise.all(
        toProcess.map(async (f) => {
          const reader = new FileReader();
          return new Promise<{ dataUrl: string; name: string }>((resolve) => {
            reader.onload = () => resolve({ dataUrl: reader.result as string, name: f.name || 'pasted-image' });
            reader.readAsDataURL(f);
          });
        }),
      ).then((newItems) => {
        setAttachedImages((prev) => [...prev, ...newItems]);
      });
    }
  }

  const currentTitle = currentConvId
    ? conversations.find((c) => c.id === currentConvId)?.title || '对话'
    : '选择或创建一个对话';

  const siderSlot = document.getElementById('sider-slot');

  const sidebarContent = (
    <div className={styles.chatContainer} style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent' }}>
      <div className={styles.sidebarHeader}>
        <Button
          className={styles.newChatBtn}
          icon={<Plus size={16} />}
          onClick={handleNewChat}
        >
          新对话
        </Button>
      </div>
      <div className={styles.convList}>
        {conversations.map((conv) => {
          const isConvStreaming = conv.id === streamingConvId && isStreaming;
          const isConvHitl = conv.id === streamingConvId && !!interruptData && !isStreaming;
          return (
            <div
              key={conv.id}
              className={`${styles.convItem} ${conv.id === currentConvId ? styles.active : ''}`}
              onClick={() => loadMessages(conv.id)}
            >
              <span className={styles.convTitle}>{conv.title}</span>
              {isConvStreaming && (
                <span className={styles.streamingDots} title="正在回复">
                  <span>.</span><span>.</span><span>.</span>
                </span>
              )}
              {isConvHitl && (
                <span className={styles.hitlBadge} title="等待审批">?</span>
              )}
              <Button
                type="text"
                size="small"
                danger
                icon={<Trash size={14} />}
                className={styles.convDelete}
                onClick={(e) => {
                  e.stopPropagation();
                  handleDeleteConv(conv.id);
                }}
              />
            </div>
          );
        })}
        {conversations.length === 0 && (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--jf-text-dim)', fontSize: 12 }}>
            暂无对话
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className={styles.chatContainer}>
      {siderSlot && createPortal(sidebarContent, siderSlot)}

      {/* ===== Chat Area ===== */}
      <div className={styles.chatArea}>
        {/* Header */}
        <div className={styles.chatHeader}>
          <span className={styles.chatTitle}>{currentTitle}</span>
          {(!editingFile || splitMode === 'chat') && <HeaderControls />}
        </div>

        {/* Messages */}
        <div className={styles.messagesContainer} ref={containerRef}>
          {loadingConv ? (
            <LogoLoading size={240} />
          ) : messages.length === 0 && !showStreamBlocks ? (
            <div className={styles.emptyState}>
              <div className={styles.emptyIcon}>
                <img
                  src="/media_resources/jellyfishlogo.png"
                  alt="JellyfishBot"
                  style={{ width: 96, height: 96, objectFit: 'contain' }}
                />
              </div>
              <p style={{ fontSize: 22, fontWeight: 600, margin: '12px 0 4px' }}>
                Hi! 我是 JellyfishBot
              </p>
              <p className={styles.emptyHint}>
                按住 <kbd>Tab</kbd> 说话，松开自动发送
              </p>
              <div className={styles.suggestionChips}>
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s.text}
                    className={styles.suggestionChip}
                    onClick={() => handleSend(s.msg)}
                  >
                    {s.emoji} {s.text}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {messages.map((msg, idx) => (
                <MessageBubble
                  key={idx}
                  role={msg.role}
                  content={msg.content}
                  toolCalls={msg.tool_calls}
                  attachments={msg.attachments}
                  conversationId={currentConvId || undefined}
                  blocks={msg.blocks}
                />
              ))}
              {showStreamBlocks && streamBlocks.length > 0 && (
                <StreamingMessage blocks={streamBlocks} isStreaming={isStreaming} />
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

          {/* Scroll to bottom button */}
          <button
            className={`${styles.scrollBottomBtn} ${isViewingStream && isStreaming && isScrolledUp() ? styles.visible : ''}`}
            onClick={resetScroll}
          >
            <CaretDown size={14} /> 回到底部
          </button>
        </div>

        {/* Streaming-elsewhere banner (frontend-connected stream on another conv) */}
        {(isStreaming || interruptData) && !isViewingStream && (
          <div className={styles.streamElsewhereBanner}>
            <span className={styles.streamElsewhereText}>
              {interruptData ? '⏸' : '⏳'}{' '}
              「{conversations.find((c) => c.id === streamingConvId)?.title || '对话'}」
              {interruptData ? '等待审批中' : '正在运行中'}
            </span>
            <div className={styles.streamElsewhereActions}>
              <Button size="small" type="link" onClick={navigateToStreamingConv}>
                查看
              </Button>
              {isStreaming && (
                <Button size="small" type="link" danger onClick={handleStop}>
                  停止
                </Button>
              )}
            </div>
          </div>
        )}

        {/* Server-streaming banner (backend still streaming after page refresh / disconnect) */}
        {!isStreaming && !interruptData && currentConvId && serverStreaming.includes(currentConvId) && (
          <div className={styles.streamElsewhereBanner}>
            <span className={styles.streamElsewhereText}>
              ⏳ 上一轮对话仍在后台运行中
            </span>
            <div className={styles.streamElsewhereActions}>
              <Button size="small" type="link" danger onClick={() => handleForceStop(currentConvId)}>
                终止并保存
              </Button>
              <Button
                size="small"
                type="link"
                onClick={async () => {
                  await checkServerStreaming();
                  if (currentConvId) loadMessages(currentConvId);
                }}
              >
                刷新状态
              </Button>
            </div>
          </div>
        )}

        {/* Server-interrupted banner (HITL pending after page refresh) */}
        {!isStreaming && !interruptData && currentConvId && serverInterrupted.includes(currentConvId) && (
          <div className={styles.streamElsewhereBanner}>
            <span className={styles.streamElsewhereText}>
              ⏸ 此对话有待审批的操作
            </span>
            <div className={styles.streamElsewhereActions}>
              <Button size="small" type="link" onClick={() => tryRestoreInterrupt(currentConvId)}>
                恢复审批
              </Button>
              <Button size="small" type="link" danger onClick={() => handleForceStop(currentConvId)}>
                终止并保存
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
          <ImageAttachment
            ref={imageAttachRef}
            images={attachedImages}
            onImagesChange={setAttachedImages}
            disabled={isStreaming}
          />
          <div className={styles.inputToolbar}>
            <Tooltip title="上传文件">
              <button
                className={`${styles.capBtn} ${attachedImages.length > 0 ? styles.capBtnActive : ''}`}
                onClick={() => imageAttachRef.current?.triggerUpload()}
                disabled={isStreaming || attachedImages.length >= 5}
              >
                <Paperclip size={16} />
              </button>
            </Tooltip>
            <div className={styles.inputToolbarDivider} />
            {CAPABILITIES.map((cap) => (
              <Tooltip key={cap.key} title={cap.label}>
                <button
                  className={`${styles.capBtn} ${capabilities.includes(cap.key) ? styles.capBtnActive : ''}`}
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
            <Tooltip title={planMode ? 'Plan Mode 已开启' : 'Plan Mode：先规划再执行'}>
              <button
                className={`${styles.capBtn} ${planMode ? styles.capBtnActive : ''}`}
                onClick={() => setPlanMode(!planMode)}
              >
                <ListChecks size={16} />
              </button>
            </Tooltip>
            <div style={{ flex: 1 }} />
            <Select
              value={selectedModel || undefined}
              onChange={setSelectedModel}
              style={{ width: 180 }}
              size="small"
              placeholder="选择模型"
              options={models.map((m) => ({ value: m.id, label: m.name }))}
              popupMatchSelectWidth={false}
            />
          </div>
          <div className={styles.inputWrapper} onPaste={handlePaste}>
            <VoiceInput
              onTranscript={(text) => handleSend(text)}
              disabled={isStreaming}
            />
            <TextArea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入消息... (Enter 发送, Shift+Enter 换行, Tab 语音)"
              autoSize={{ minRows: 1, maxRows: 6 }}
              variant="borderless"
              disabled={isStreaming}
              style={{ color: 'var(--jf-text)', fontSize: 14, resize: 'none' }}
            />
            {isStreaming && isViewingStream ? (
              <Button
                danger
                type="primary"
                icon={<Stop size={18} weight="fill" />}
                onClick={handleStop}
                style={{ borderRadius: 'var(--jf-radius-md)', flexShrink: 0 }}
              />
            ) : (
              <Button
                type="primary"
                icon={<PaperPlaneRight size={18} weight="fill" />}
                onClick={() => handleSend()}
                disabled={(!inputValue.trim() && attachedImages.length === 0) || isStreaming || !!interruptData}
                style={{ borderRadius: 'var(--jf-radius-md)', flexShrink: 0 }}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

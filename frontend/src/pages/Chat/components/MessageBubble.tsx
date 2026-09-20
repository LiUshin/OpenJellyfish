import { memo, useMemo } from 'react';
import type { ToolCallInfo, ThinkingBlock as ThinkingBlockType, ToolBlock, SubagentBlock, StreamBlock } from '../types';
import type { MessageAttachment, MessageBlock } from '../../../types';
import { renderMarkdown } from '../markdown';
import { attachmentUrl } from '../../../services/api';
import AssistantResponseBody from './AssistantResponseBody';
import type { PlanStep } from '../../../stores/streamContext';
import styles from '../chat.module.css';

const JELLYFISH_AVATAR_SRC = '/media_resources/jellyfishlogo.png';

function AttachmentGallery({ attachments, convId }: {
  attachments: MessageAttachment[];
  convId?: string;
}) {
  const images = attachments.filter(a => a.type === 'image');
  if (images.length === 0 || !convId) return null;

  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: 8,
      marginTop: 8, marginBottom: 4,
    }}>
      {images.map((img, i) => {
        const url = attachmentUrl(convId, img.path);
        return (
          <img
            key={i}
            src={url}
            alt={img.filename}
            loading="lazy"
            onClick={() => window.open(url, '_blank')}
            style={{
              maxWidth: 240, maxHeight: 180, borderRadius: 'var(--jf-radius-md)',
              cursor: 'pointer', objectFit: 'cover',
              border: '1px solid var(--jf-border)',
            }}
          />
        );
      })}
    </div>
  );
}

function toThinkingBlock(b: MessageBlock & { type: 'thinking' }): ThinkingBlockType {
  return { type: 'thinking', content: b.content, collapsed: true };
}

function toToolBlock(b: MessageBlock & { type: 'tool' }): ToolBlock {
  return {
    type: 'tool',
    name: b.name,
    args: b.args || '',
    result: b.result || '',
    done: b.done !== false,
    resultCollapsed: true,
  };
}

function toSubagentBlock(b: MessageBlock & { type: 'subagent' }): SubagentBlock {
  return {
    type: 'subagent',
    name: b.name || '',
    task: b.task || '',
    status: (b.status as SubagentBlock['status']) || 'done',
    content: b.content || '',
    tools: (b.tools || []).map(t => ({ name: t.name, done: t.done !== false })),
    timeline: (b.timeline || []).map(e => ({
      kind: e.kind as 'text' | 'tool' | 'thinking',
      content: e.content,
      toolName: e.toolName,
      toolDone: e.toolDone,
    })),
    collapsed: true,
    done: b.done !== false,
    subagentId: b.subagent_id,
  };
}

function extractPlanSteps(blocks: MessageBlock[]): PlanStep[] | null {
  let lastTodos: PlanStep[] | null = null;
  for (const b of blocks) {
    if (b.type === 'tool' && b.name === 'write_todos') {
      try {
        const parsed = JSON.parse(b.args);
        const todos = parsed?.todos;
        if (Array.isArray(todos) && todos.length > 0) {
          lastTodos = todos.map((t: { content?: string; status?: string }) => ({
            content: t.content ?? '',
            status: t.status ?? 'pending',
          }));
        }
      } catch { /* ignore */ }
    }
  }
  return lastTodos;
}

function BlocksRenderer({ blocks }: { blocks: MessageBlock[] }) {
  const normalized = useMemo<StreamBlock[]>(() => blocks.map(block => {
    if (block.type === 'tool') return toToolBlock(block);
    if (block.type === 'subagent') return toSubagentBlock(block);
    if (block.type === 'thinking') return toThinkingBlock(block);
    return block;
  }), [blocks]);
  const planSteps = useMemo(() => extractPlanSteps(blocks), [blocks]);
  return <AssistantResponseBody blocks={normalized} isStreaming={false} planSteps={planSteps} />;
}

function LegacyResponse({ content, toolCalls }: { content: string; toolCalls?: ToolCallInfo[] }) {
  const blocks = useMemo<StreamBlock[]>(() => [
    ...(toolCalls || []).map(tc => ({ type: 'tool' as const, ...tc, done: true, resultCollapsed: true })),
    ...(content ? [{ type: 'text' as const, content }] : []),
  ], [content, toolCalls]);
  return <AssistantResponseBody blocks={blocks} isStreaming={false} />;
}

interface Props {
  role: string;
  content: string;
  toolCalls?: ToolCallInfo[];
  attachments?: MessageAttachment[];
  conversationId?: string;
  blocks?: MessageBlock[];
}

function MessageBubbleImpl({ role, content, toolCalls, attachments, conversationId, blocks }: Props) {
  const isUser = role === 'user';

  if (isUser) {
    return (
      <div className={styles.messageBubbleUser}>
        <div className={styles.userBubbleContent}>
          <div
            className={styles.userBubbleText}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
          />
          {attachments && attachments.length > 0 && (
            <AttachmentGallery attachments={attachments} convId={conversationId} />
          )}
        </div>
        <div className={styles.messageAvatar} data-role="user">U</div>
      </div>
    );
  }

  const hasBlocks = blocks && blocks.length > 0;

  return (
    <div className={styles.messageBubble}>
      <div className={styles.messageAvatar} data-role="assistant">
        <img
          src={JELLYFISH_AVATAR_SRC}
          alt=""
          width={32}
          height={32}
          style={{ display: 'block', borderRadius: 'inherit', objectFit: 'cover' }}
        />
      </div>
      <div className={styles.messageBody}>
        {hasBlocks ? (
          <BlocksRenderer blocks={blocks} />
        ) : (
          <LegacyResponse content={content} toolCalls={toolCalls} />
        )}
        {attachments && attachments.length > 0 && (
          <AttachmentGallery attachments={attachments} convId={conversationId} />
        )}
      </div>
    </div>
  );
}

// React.memo 默认浅比较 props。messages 数组里每条都是稳定引用，
// 输入框打字 / 顶层任意 state 更新都不会再触发已渲染消息的 re-render。
const MessageBubble = memo(MessageBubbleImpl);
export default MessageBubble;

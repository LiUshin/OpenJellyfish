import { useState } from 'react';
import { CheckCircle } from '@phosphor-icons/react';
import type { ToolCallInfo, ThinkingBlock as ThinkingBlockType, ToolBlock, SubagentBlock } from '../types';
import type { MessageAttachment, MessageBlock } from '../../../types';
import { renderMarkdown, escapeHtml } from '../markdown';
import { attachmentUrl } from '../../../services/api';
import ThinkingBlockCmp from './ThinkingBlock';
import ToolIndicator from './ToolIndicator';
import SubagentCard from './SubagentCard';
import PlanTracker from './PlanTracker';
import type { PlanStep } from '../../../stores/streamContext';
import styles from '../chat.module.css';

const JELLYFISH_AVATAR_SRC = '/media_resources/jellyfishlogo.png';

function HistoryToolCall({ tc }: { tc: ToolCallInfo }) {
  const [expanded, setExpanded] = useState(false);
  const hasArgs = tc.args?.trim().length > 0;
  const hasResult = tc.result?.trim().length > 0;

  return (
    <>
      <div
        className={`${styles.toolPill} ${styles.toolPillDone}`}
        onClick={() => (hasArgs || hasResult) && setExpanded(!expanded)}
        style={{ cursor: hasArgs || hasResult ? 'pointer' : 'default' }}
      >
        <CheckCircle size={14} weight="fill" />
        <span className={styles.toolPillName}>{escapeHtml(tc.name)}</span>
        {(hasArgs || hasResult) && (
          <span className={styles.toolPillChevron}>{expanded ? '▾' : '▸'}</span>
        )}
      </div>
      {expanded && (
        <div className={styles.toolExpandedDetail}>
          {hasArgs && <div className={styles.toolStreamPreview}>{tc.args}</div>}
          {hasResult && <div className={styles.toolResultPreview}>{tc.result}</div>}
        </div>
      )}
    </>
  );
}

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

const PLAN_TOOL_NAMES = new Set(['write_todos', 'propose_plan']);

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
  const planSteps = extractPlanSteps(blocks);
  let planRendered = false;

  return (
    <>
      {blocks.map((block, i) => {
        switch (block.type) {
          case 'thinking':
            return <ThinkingBlockCmp key={`thinking-${i}`} block={toThinkingBlock(block)} />;
          case 'text':
            return (
              <div
                key={`text-${i}`}
                className={`${styles.messageContent} ${styles.agentContent}`}
                dangerouslySetInnerHTML={{ __html: renderMarkdown(block.content) }}
              />
            );
          case 'tool':
            if (PLAN_TOOL_NAMES.has(block.name) && planSteps) {
              if (!planRendered) {
                planRendered = true;
                return <PlanTracker key={`plan-${i}`} steps={planSteps} defaultCollapsed />;
              }
              return null;
            }
            return <ToolIndicator key={`tool-${i}`} block={toToolBlock(block)} />;
          case 'subagent':
            return <SubagentCard key={`subagent-${i}`} block={toSubagentBlock(block)} />;
          default:
            return null;
        }
      })}
    </>
  );
}

interface Props {
  role: string;
  content: string;
  toolCalls?: ToolCallInfo[];
  attachments?: MessageAttachment[];
  conversationId?: string;
  blocks?: MessageBlock[];
}

export default function MessageBubble({ role, content, toolCalls, attachments, conversationId, blocks }: Props) {
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
          <>
            {toolCalls && toolCalls.length > 0 && (
              <div className={styles.toolPillGroup}>
                {toolCalls.map((tc, i) => <HistoryToolCall key={i} tc={tc} />)}
              </div>
            )}
            <div
              className={`${styles.messageContent} ${styles.agentContent}`}
              dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
            />
          </>
        )}
        {attachments && attachments.length > 0 && (
          <AttachmentGallery attachments={attachments} convId={conversationId} />
        )}
      </div>
    </div>
  );
}

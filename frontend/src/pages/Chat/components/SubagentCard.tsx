import { memo, useState } from 'react';
import { Robot, CaretDown, CaretRight, Wrench, Brain } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import type { SubagentBlock, SubagentTimelineEntry } from '../types';
import { renderMarkdown, escapeHtml } from '../markdown';
import styles from '../chat.module.css';

const STATUS_COLOR: Record<SubagentBlock['status'], string> = {
  preparing: 'var(--jf-warning)',
  running: 'var(--jf-accent)',
  done: 'var(--jf-primary)',
};

interface Props {
  block: SubagentBlock;
}

function ToolRow({ entry }: { entry: SubagentTimelineEntry }) {
  const { t } = useTranslation();
  return (
    <div className={`${styles.subagentTool} ${entry.toolDone ? styles.done : ''}`}>
      <span className={styles.subagentToolDot} />
      <span className={styles.subagentToolName}>
        <Wrench size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} weight="duotone" />
        {escapeHtml(entry.toolName || '')}
      </span>
      <span className={styles.subagentToolStatus}>
        {entry.toolDone ? '✓' : t('subagentCard.calling')}
      </span>
    </div>
  );
}

function TextChunk({ content, isLast, isStreaming }: { content: string; isLast: boolean; isStreaming: boolean }) {
  if (!content) return null;
  return (
    <div
      className={`${styles.subagentStreamContent} ${isLast && isStreaming ? styles.streamingCursor : ''}`}
      dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
    />
  );
}

function ThinkingChunk({ content }: { content: string }) {
  if (!content) return null;
  return (
    <div className={styles.subagentThinking}>
      <Brain size={12} weight="duotone" style={{ verticalAlign: 'middle', marginRight: 4, color: 'var(--jf-secondary)' }} />
      <span style={{ opacity: 0.7, fontSize: '0.85em' }}>{escapeHtml(content)}</span>
    </div>
  );
}

function TimelineRenderer({ timeline, isStreaming }: { timeline: SubagentTimelineEntry[]; isStreaming: boolean }) {
  return (
    <>
      {timeline.map((entry, i) => {
        const isLast = i === timeline.length - 1;
        switch (entry.kind) {
          case 'tool':
            return <ToolRow key={i} entry={entry} />;
          case 'thinking':
            return <ThinkingChunk key={i} content={entry.content || ''} />;
          case 'text':
            return <TextChunk key={i} content={entry.content || ''} isLast={isLast} isStreaming={isStreaming} />;
          default:
            return null;
        }
      })}
    </>
  );
}

function LegacyRenderer({ block }: { block: SubagentBlock }) {
  const { t } = useTranslation();
  return (
    <>
      {block.tools.map((tool, i) => (
        <div key={i} className={`${styles.subagentTool} ${tool.done ? styles.done : ''}`}>
          <span className={styles.subagentToolDot} />
          <span className={styles.subagentToolName}>
            <Wrench size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} weight="duotone" />
            {escapeHtml(tool.name)}
          </span>
          <span className={styles.subagentToolStatus}>
            {tool.done ? '✓' : t('subagentCard.calling')}
          </span>
        </div>
      ))}
      {block.content && (
        <div
          className={styles.subagentStreamContent}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(block.content) }}
        />
      )}
    </>
  );
}

function SubagentCard({ block }: Props) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(!block.done);

  const statusText = {
    preparing: t('subagentCard.preparing'),
    running: t('subagentCard.running'),
    done: t('subagentCard.done'),
  }[block.status];

  const hasTimeline = block.timeline && block.timeline.length > 0;
  const isStreaming = !block.done;

  return (
    <div className={`${styles.subagentCard} ${block.done ? styles.done : ''}`}>
      <div className={styles.subagentHeader} onClick={() => setExpanded(!expanded)}>
        <span className={styles.subagentIcon}>
          <Robot size={18} weight="duotone" color="var(--jf-secondary)" />
        </span>
        <span className={styles.subagentName}>{escapeHtml(block.name || 'Subagent')}</span>
        <span
          className={`${styles.subagentStatus} ${block.done ? styles.done : ''}`}
          style={{ color: STATUS_COLOR[block.status] }}
        >
          {statusText}
        </span>
        <span className={styles.subagentToggle}>
          {expanded ? <CaretDown size={14} weight="bold" /> : <CaretRight size={14} weight="bold" />}
        </span>
      </div>

      {block.task && <div className={styles.subagentTask}>{block.task}</div>}

      {expanded && (
        <div className={styles.subagentStream}>
          {hasTimeline
            ? <TimelineRenderer timeline={block.timeline} isStreaming={isStreaming} />
            : <LegacyRenderer block={block} />
          }
        </div>
      )}
    </div>
  );
}

// block 引用在指纹 flush 下稳定；已完成的 subagent 卡片流式期间不再重渲染。
export default memo(SubagentCard);

import { memo, useState } from 'react';
import { Wrench, CircleNotch, CheckCircle } from '@phosphor-icons/react';
import type { ToolBlock } from '../types';
import { escapeHtml } from '../markdown';
import styles from '../chat.module.css';

interface Props {
  block: ToolBlock;
}

function ToolIndicator({ block }: Props) {
  const [expanded, setExpanded] = useState(false);
  const hasArgs = block.args.trim().length > 0;
  const hasResult = block.result.trim().length > 0;
  const longResult = block.result.length > 500;

  if (!block.done) {
    return (
      <div className={styles.toolPill}>
        <span className={styles.toolPillSpin}>
          <CircleNotch size={14} weight="bold" />
        </span>
        <span className={styles.toolPillName}>{escapeHtml(block.name)}</span>
        {hasArgs && (
          <span className={styles.toolPillArgs}>{block.args.slice(0, 60)}</span>
        )}
      </div>
    );
  }

  return (
    <>
      <div
        className={`${styles.toolPill} ${styles.toolPillDone}`}
        onClick={() => (hasArgs || hasResult) && setExpanded(!expanded)}
        style={{ cursor: hasArgs || hasResult ? 'pointer' : 'default' }}
      >
        <CheckCircle size={14} weight="fill" />
        <span className={styles.toolPillName}>{escapeHtml(block.name)}</span>
        {(hasArgs || hasResult) && (
          <span className={styles.toolPillChevron}>{expanded ? '▾' : '▸'}</span>
        )}
      </div>
      {expanded && (
        <div className={styles.toolExpandedDetail}>
          {hasArgs && (
            <div className={styles.toolStreamPreview}>{block.args}</div>
          )}
          {hasResult && (
            <div
              className={`${styles.toolResultPreview} ${longResult && !expanded ? styles.collapsedResult : ''}`}
            >
              {block.result}
            </div>
          )}
        </div>
      )}
    </>
  );
}

// block 引用在 streamContext 的指纹 flush 下保持稳定（未变即复用同一对象），
// memo 让已完成的 tool pill 在流式期间不再每帧重渲染。
export default memo(ToolIndicator);

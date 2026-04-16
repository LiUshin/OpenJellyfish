import { useState } from 'react';
import type { ThinkingBlock as ThinkingBlockType } from '../types';
import styles from '../chat.module.css';

interface Props {
  block: ThinkingBlockType;
  isStreaming?: boolean;
}

export default function ThinkingBlock({ block, isStreaming }: Props) {
  const [collapsed, setCollapsed] = useState(block.collapsed);
  const showDots = !collapsed && isStreaming;

  return (
    <div className={`${styles.thinkingBlock} ${collapsed ? styles.collapsed : ''}`}>
      <div className={styles.thinkingHeader} onClick={() => setCollapsed(!collapsed)}>
        <span className={styles.thinkingLabel}>
          {collapsed ? '▶' : '▼'} thinking
        </span>
        {showDots && (
          <span className={styles.thinkingDots}>
            <span />
            <span />
            <span />
          </span>
        )}
      </div>
      {!collapsed && (
        <div className={`${styles.thinkingContent} ${isStreaming ? styles.streamingCursor : ''}`}>
          {block.content}
        </div>
      )}
    </div>
  );
}

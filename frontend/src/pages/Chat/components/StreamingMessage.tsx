import { memo } from 'react';
import type { StreamBlock } from '../types';
import { renderMarkdown } from '../markdown';
import ThinkingBlockCmp from './ThinkingBlock';
import ToolIndicator from './ToolIndicator';
import SubagentCard from './SubagentCard';
import styles from '../chat.module.css';

const JELLYFISH_AVATAR_SRC = '/media_resources/jellyfishlogo.png';

interface Props {
  blocks: StreamBlock[];
  isStreaming: boolean;
}

function AssistantAvatar() {
  return (
    <div className={styles.messageAvatar} data-role="assistant">
      <img
        src={JELLYFISH_AVATAR_SRC}
        alt=""
        width={32}
        height={32}
        style={{ display: 'block', borderRadius: 'inherit', objectFit: 'cover' }}
      />
    </div>
  );
}

function StreamingMessage({ blocks, isStreaming }: Props) {
  if (blocks.length === 0 && isStreaming) {
    return (
      <div className={styles.messageBubble}>
        <AssistantAvatar />
        <div className={styles.messageBody}>
          <div className={`${styles.messageContent} ${styles.agentContent} ${styles.streamingCursor}`} />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.messageBubble}>
      <AssistantAvatar />
      <div className={styles.messageBody}>
        {blocks.map((block, i) => {
          const isLast = i === blocks.length - 1;
          switch (block.type) {
            case 'thinking':
              return (
                <ThinkingBlockCmp
                  key={`thinking-${i}`}
                  block={block}
                  isStreaming={isLast && isStreaming}
                />
              );
            case 'text':
              return (
                <div
                  key={`text-${i}`}
                  className={`${styles.messageContent} ${styles.agentContent} ${isLast && isStreaming ? styles.streamingCursor : ''}`}
                  dangerouslySetInnerHTML={{
                    __html: renderMarkdown(block.content),
                  }}
                />
              );
            case 'tool':
              return <ToolIndicator key={`tool-${i}`} block={block} />;
            case 'subagent':
              return <SubagentCard key={`subagent-${i}`} block={block} />;
            default:
              return null;
          }
        })}
      </div>
    </div>
  );
}

export default memo(StreamingMessage);

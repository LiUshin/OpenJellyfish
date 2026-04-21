import { memo, type ComponentType } from 'react';
import type { StreamBlock, ToolBlock } from '../types';
import { renderMarkdown } from '../markdown';
import ThinkingBlockCmp from './ThinkingBlock';
import ToolIndicator from './ToolIndicator';
import SubagentCard from './SubagentCard';
import styles from '../chat.module.css';

const JELLYFISH_AVATAR_SRC = '/media_resources/jellyfishlogo.png';

export interface ToolRendererProps {
  block: ToolBlock;
}

interface Props {
  blocks: StreamBlock[];
  isStreaming: boolean;
  /** 自定义工具块渲染器；不传则用默认 ToolIndicator（admin 完整可展开版）。
   *  service-chat 入口传 ServiceToolBadge —— 友好状态条、不展示 args/result。 */
  toolRenderer?: ComponentType<ToolRendererProps>;
  /** 隐藏 subagent 卡片（service-chat 默认隐藏，避免向消费者泄露内部子流程）。 */
  hideSubagents?: boolean;
  /** 自定义头像 URL；不传用 jellyfish 默认 logo。 */
  avatarSrc?: string;
}

function AssistantAvatar({ src }: { src: string }) {
  return (
    <div className={styles.messageAvatar} data-role="assistant">
      <img
        src={src}
        alt=""
        width={32}
        height={32}
        style={{ display: 'block', borderRadius: 'inherit', objectFit: 'cover' }}
      />
    </div>
  );
}

function StreamingMessage({
  blocks,
  isStreaming,
  toolRenderer,
  hideSubagents = false,
  avatarSrc = JELLYFISH_AVATAR_SRC,
}: Props) {
  const ToolComp = toolRenderer ?? ToolIndicator;

  if (blocks.length === 0 && isStreaming) {
    return (
      <div className={styles.messageBubble}>
        <AssistantAvatar src={avatarSrc} />
        <div className={styles.messageBody}>
          <div className={`${styles.messageContent} ${styles.agentContent} ${styles.streamingCursor}`} />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.messageBubble}>
      <AssistantAvatar src={avatarSrc} />
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
              return <ToolComp key={`tool-${i}`} block={block} />;
            case 'subagent':
              if (hideSubagents) return null;
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

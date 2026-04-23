import { memo, type ComponentType } from 'react';
import type { StreamBlock, ToolBlock } from '../types';
import { renderMarkdown } from '../markdown';
import ThinkingBlockCmp from './ThinkingBlock';
import ToolIndicator from './ToolIndicator';
import StreamingFilePreview from './StreamingFilePreview';
import SubagentCard from './SubagentCard';
import ScheduledTaskCard from './ScheduledTaskCard';
import styles from '../chat.module.css';

/** 这两类工具不走通用 ToolIndicator/toolRenderer，统一用 StreamingFilePreview 的
 *  IDE 风格代码块 + 打字机渲染（admin / consumer 都一样）。 */
const FILE_WRITE_TOOLS = new Set(['write_file', 'edit_file']);

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
  /** 定时任务卡片使用 friendly 变体（系统通知样式，隐藏 task_id/scope 等内部字段）。
   *  service-chat 设为 true，admin 默认 false（显示完整元数据）。 */
  scheduledTaskFriendlyMode?: boolean;
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
  scheduledTaskFriendlyMode = false,
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
              // 定时任务卡片：admin/service 都用同一个组件，service-chat 通过自定义
              // toolRenderer 注入 friendlyMode；这里 admin 路径直接渲染默认变体。
              if (block.name === 'scheduled_task') {
                return (
                  <ScheduledTaskCard
                    key={`sched-${i}`}
                    block={block}
                    friendlyMode={scheduledTaskFriendlyMode}
                  />
                );
              }
              if (FILE_WRITE_TOOLS.has(block.name)) {
                return (
                  <StreamingFilePreview
                    key={`tool-${i}`}
                    block={block}
                    isStreaming={isLast && isStreaming}
                  />
                );
              }
              return <ToolComp key={`tool-${i}`} block={block} />;
            case 'subagent':
              if (hideSubagents) return null;
              return <SubagentCard key={`subagent-${i}`} block={block} />;
            case 'auto_approve':
              // YOLO 自动批准已改为输入区底部小 tag 提示，消息流内不再渲染显眼徽章。
              return null;
            default:
              return null;
          }
        })}
      </div>
    </div>
  );
}

export default memo(StreamingMessage);

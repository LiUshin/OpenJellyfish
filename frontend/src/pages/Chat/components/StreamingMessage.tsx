import { memo, useMemo, type ComponentType, type ReactNode } from 'react';
import type { StreamBlock, TextBlock, ToolBlock } from '../types';
import { renderMarkdown, renderStreamingMarkdown } from '../markdown';
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

/** 文本块渲染独立成 memo 组件：block 引用（指纹 flush 下）稳定时不重跑 markdown；
 *  只有正在流式的尾部块每帧走 renderStreamingMarkdown（增量、轻量）。 */
const StreamTextBlock = memo(function StreamTextBlock({
  block,
  isStreamingTail,
}: {
  block: TextBlock;
  isStreamingTail: boolean;
}) {
  const html = useMemo(
    () => (isStreamingTail ? renderStreamingMarkdown(block.content) : renderMarkdown(block.content)),
    [block, isStreamingTail],
  );
  return (
    <div
      className={`${styles.messageContent} ${styles.agentContent} ${isStreamingTail ? styles.streamingCursor : ''}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
});

/** 已完成（非流式尾部）的 block 用 content-visibility 包一层，让浏览器跳过
 *  屏外元素的 layout/paint —— 长会话大量 block 时显著降主线程压力。 */
function BlockShell({ settled, children }: { settled: boolean; children: ReactNode }) {
  return <div className={settled ? styles.streamBlockSettled : undefined}>{children}</div>;
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
          const streamingTail = isLast && isStreaming;
          // 非流式尾部 = 已"定型"，用 content-visibility 跳过屏外渲染。
          const settled = !streamingTail;
          switch (block.type) {
            case 'thinking':
              return (
                <BlockShell key={`thinking-${i}`} settled={settled}>
                  <ThinkingBlockCmp block={block} isStreaming={streamingTail} />
                </BlockShell>
              );
            case 'text':
              // 流式中的最后一个文本块走增量渲染（稳定前缀缓存 + 尾部轻量），
              // 避免超长 response 每帧重解析全文导致的 O(n²) 卡顿；
              // 非流式 / 非最后块走完整 renderMarkdown（命中缓存、含高亮）。
              return (
                <BlockShell key={`text-${i}`} settled={settled}>
                  <StreamTextBlock block={block} isStreamingTail={streamingTail} />
                </BlockShell>
              );
            case 'tool':
              // 定时任务卡片：admin/service 都用同一个组件，service-chat 通过自定义
              // toolRenderer 注入 friendlyMode；这里 admin 路径直接渲染默认变体。
              if (block.name === 'scheduled_task') {
                return (
                  <BlockShell key={`sched-${i}`} settled={settled}>
                    <ScheduledTaskCard block={block} friendlyMode={scheduledTaskFriendlyMode} />
                  </BlockShell>
                );
              }
              if (FILE_WRITE_TOOLS.has(block.name)) {
                return (
                  <BlockShell key={`tool-${i}`} settled={settled}>
                    <StreamingFilePreview block={block} isStreaming={streamingTail} />
                  </BlockShell>
                );
              }
              // 普通工具 pill 是 inline-flex，需保持「横向优先排列、展开另起一行」
              // 的历史行为（与 MessageBubble.BlocksRenderer 一致）。不能用 BlockShell
              // 的块级 <div> 包裹，否则每个 pill 各占一行。pill 体积小，
              // content-visibility 收益可忽略，直接渲染即可（ToolComp 已 memo）。
              return <ToolComp key={`tool-${i}`} block={block} />;
            case 'subagent':
              if (hideSubagents) return null;
              return (
                <BlockShell key={`subagent-${i}`} settled={settled}>
                  <SubagentCard block={block} />
                </BlockShell>
              );
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

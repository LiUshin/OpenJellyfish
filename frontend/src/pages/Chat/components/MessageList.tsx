import {
  forwardRef,
  memo,
  useCallback,
  useImperativeHandle,
  useMemo,
  useRef,
} from 'react';
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso';
import type { Message } from '../../../types';
import MessageBubble from './MessageBubble';

/**
 * MessageList — 基于 react-virtuoso 的虚拟化消息列表。
 *
 * 设计要点：
 * 1. 只渲染可视区内的消息节点，长会话（30 → 100 → 1000+）的初次挂载、
 *    滚动 fps 与内存占用都不会随消息数线性退化。
 * 2. 使用 customScrollParent 复用外层 .messagesContainer 的 overflow/padding，
 *    避免改 CSS、避免破坏「回到底部」浮层按钮的 position:absolute 上下文。
 * 3. footerSlot 通过 Virtuoso context 传递，承载 StreamingMessage / PlanTracker /
 *    ApprovalCard 这些「位于消息流末尾」的非历史节点。
 * 4. followStream 决定是否启用 followOutput="auto"——只有用户停留在底部时
 *    才会自动滚动，与旧 useSmartScroll 行为一致。
 * 5. 通过 ref 暴露 scrollToBottom / resetScroll / isScrolledUp，
 *    让父组件以最少改动迁移旧 useSmartScroll 调用点。
 */

export interface MessageListHandle {
  /** 流式 chunk 高频触发：只做 scrollTop=scrollHeight 一步贴底，不重测 LAST item，
   *  避免「先跳到 data 底，再跳到 footer 底」的双跳闪烁。 */
  scrollToBottom: (force?: boolean) => void;
  /** 切换对话 / 首次挂载用：双步走（先 Virtuoso 把 LAST 测进来，再外层 scrollTop 把 footer 带进来）。 */
  resetScroll: () => void;
  isScrolledUp: () => boolean;
}

interface Props {
  messages: Message[];
  conversationId: string | null;
  /** 列表底部需要持续渲染的「非历史」节点（流式消息 / plan / 审批卡）。 */
  footerSlot: React.ReactNode;
  /** 外层滚动容器（一般是 .messagesContainer）。 */
  scrollParent: HTMLElement | null;
  /** 是否启用 follow tail（一般为 isStreaming && isViewingStream）。 */
  followStream: boolean;
  /** 用户离开 / 回到底部时回调。父组件用这个驱动「回到底部」按钮可见性。 */
  onAtBottomChange?: (atBottom: boolean) => void;
}

interface FooterContext {
  footerSlot: React.ReactNode;
}

const Footer = memo(function Footer({ context }: { context?: FooterContext }) {
  if (!context?.footerSlot) return null;
  return <>{context.footerSlot}</>;
});

const components = { Footer };

const MessageList = forwardRef<MessageListHandle, Props>(function MessageList(
  { messages, conversationId, footerSlot, scrollParent, followStream, onAtBottomChange },
  ref,
) {
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  // atBottomRef 只用于 isScrolledUp 查询；不进 state 避免 follow tail 时频繁 re-render。
  const atBottomRef = useRef(true);
  // 始终持有最新的 scrollParent，避免 useImperativeHandle 闭包过期。
  const scrollParentRef = useRef<HTMLElement | null>(scrollParent);
  scrollParentRef.current = scrollParent;
  // 持最新 onAtBottomChange，避免 callback 引用变化导致 handleAtBottom 重建。
  const onAtBottomChangeRef = useRef(onAtBottomChange);
  onAtBottomChangeRef.current = onAtBottomChange;

  // 「轻量贴底」——只把外层容器 scrollTop 拉到 scrollHeight。适用于流式 chunk 高频
  // 触发场景：messages 数组在 streaming 期间不变，只有 footer（StreamingMessage）的
  // 高度在长，根本不需要 Virtuoso 重测 LAST item。这一步不会引发「先跳 data 底 / 再
  // 跳 footer 底」的双跳闪烁。
  const scrollFooterIntoView = useCallback(() => {
    const el = scrollParentRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // 「双步定位」=「Virtuoso 把最后一条 data item 测量并滚入视野」+「外层
  // scrollTop=scrollHeight 把 footer（StreamingMessage / PlanTracker / ApprovalCard）
  // 也带进视野」。仅用于切换对话 / 首次挂载，那时 Virtuoso 还没测量过最后一条 item，
  // 单纯 scrollTop=scrollHeight 反映的只是已渲染头部高度 → 会落在中间，必须先调
  // scrollToIndex 让 Virtuoso 把 LAST item 测好、挂上 DOM，再用 scrollTop 一次性贴底。
  const scrollToAbsoluteBottom = useCallback(() => {
    virtuosoRef.current?.scrollToIndex({
      index: 'LAST',
      behavior: 'auto',
      align: 'end',
    });
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        scrollFooterIntoView();
      });
    });
  }, [scrollFooterIntoView]);

  useImperativeHandle(
    ref,
    () => ({
      // streaming 高频路径：永远走轻量贴底，避免双跳闪烁。
      scrollToBottom: (force = false) => {
        if (!atBottomRef.current && !force) return;
        scrollFooterIntoView();
      },
      // 切对话 / 重置：走双步定位。
      resetScroll: () => {
        atBottomRef.current = true;
        onAtBottomChangeRef.current?.(true);
        scrollToAbsoluteBottom();
      },
      isScrolledUp: () => !atBottomRef.current,
    }),
    [scrollFooterIntoView, scrollToAbsoluteBottom],
  );

  const handleAtBottom = useCallback((atBottom: boolean) => {
    if (atBottomRef.current === atBottom) return;
    atBottomRef.current = atBottom;
    onAtBottomChangeRef.current?.(atBottom);
  }, []);

  const itemContent = useCallback(
    (_idx: number, msg: Message) => (
      <MessageBubble
        role={msg.role}
        content={msg.content}
        toolCalls={msg.tool_calls}
        attachments={msg.attachments}
        conversationId={conversationId || undefined}
        blocks={msg.blocks}
      />
    ),
    [conversationId],
  );

  const context = useMemo<FooterContext>(
    () => ({ footerSlot }),
    [footerSlot],
  );

  // computeItemKey 让 Virtuoso 在消息追加 / swap 时正确复用 DOM 节点。
  // 没有 stable id，用「timestamp + role」做指纹；落地后顺序稳定即可。
  const computeItemKey = useCallback((idx: number, msg: Message) => {
    return msg.timestamp
      ? `${msg.timestamp}-${msg.role}`
      : `${idx}-${msg.role}-${msg.content.length}`;
  }, []);

  // scrollParent 还没挂载时不渲染——避免 Virtuoso 内部按 window 处理。
  if (!scrollParent) return null;

  return (
    <Virtuoso
      ref={virtuosoRef}
      data={messages}
      itemContent={itemContent}
      computeItemKey={computeItemKey}
      followOutput={followStream ? 'auto' : false}
      atBottomStateChange={handleAtBottom}
      atBottomThreshold={60}
      increaseViewportBy={{ top: 600, bottom: 600 }}
      components={components}
      context={context}
      customScrollParent={scrollParent}
    />
  );
});

export default MessageList;

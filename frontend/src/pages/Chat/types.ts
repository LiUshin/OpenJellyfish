export type StreamBlock =
  | ThinkingBlock
  | TextBlock
  | ToolBlock
  | SubagentBlock
  | AutoApproveBlock;

export interface ThinkingBlock {
  type: 'thinking';
  content: string;
  collapsed: boolean;
}

export interface TextBlock {
  type: 'text';
  content: string;
}

export interface ToolBlock {
  type: 'tool';
  name: string;
  args: string;
  result: string;
  done: boolean;
  resultCollapsed: boolean;
}

export interface SubagentTimelineEntry {
  kind: 'text' | 'tool' | 'thinking';
  content?: string;
  toolName?: string;
  toolDone?: boolean;
}

export interface SubagentBlock {
  type: 'subagent';
  name: string;
  task: string;
  status: 'preparing' | 'running' | 'done';
  content: string;
  tools: { name: string; done: boolean }[];
  timeline: SubagentTimelineEntry[];
  collapsed: boolean;
  done: boolean;
  subagentId?: number;
}

/** YOLO 模式下后端自动批准 HITL 时插入的标记块（仅用于兼容历史消息反序列化）。
 *  当前实现不再向消息流追加该块，亦不再渲染显眼徽章；
 *  改为在 Chat 输入区底部显示一个不显眼的 yolo 小 tag（见 chat.module.css/.yoloFooterTag）。 */
export interface AutoApproveBlock {
  type: 'auto_approve';
  count: number;
  actions: { name: string; args: unknown }[];
}

export interface ToolCallInfo {
  name: string;
  args: string;
  result: string;
}

export type StreamBlock =
  | ThinkingBlock
  | TextBlock
  | ToolBlock
  | SubagentBlock;

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

export interface ToolCallInfo {
  name: string;
  args: string;
  result: string;
}

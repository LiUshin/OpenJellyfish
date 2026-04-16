export interface User {
  user_id: string;
  username: string;
}

export interface AuthResult {
  token: string;
  user_id: string;
  username: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
}

export interface MessageAttachment {
  type: 'image' | 'file';
  filename: string;
  path: string;
}

export type MessageBlock =
  | { type: 'thinking'; content: string }
  | { type: 'text'; content: string }
  | { type: 'tool'; name: string; args: string; result: string; done?: boolean }
  | { type: 'subagent'; name: string; task: string; status: string; content: string; tools: { name: string; done: boolean }[]; timeline?: { kind: string; content?: string; toolName?: string; toolDone?: boolean }[]; done?: boolean; subagent_id?: number };

export interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
  tool_calls?: { name: string; args: string; result: string }[];
  attachments?: MessageAttachment[];
  blocks?: MessageBlock[];
}

export interface ConversationDetail {
  id: string;
  title: string;
  messages: Message[];
}

export interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
  modified_at?: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  provider?: string;
}

export interface ModelsResponse {
  models: ModelInfo[];
  default: string;
}

export interface SystemPromptResponse {
  prompt: string;
  is_default: boolean;
}

export interface PromptVersion {
  id: string;
  label: string;
  note: string;
  timestamp: string;
  char_count: number;
  content?: string;
}

export interface SubagentConfig {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  model?: string;
  tools: string[];
  enabled: boolean;
  builtin?: boolean;
}

export interface SubagentListResponse {
  subagents: SubagentConfig[];
  available_tools: string[];
}

export interface BatchSheetInfo {
  name: string;
  row_count: number;
  headers: string[];
}

export interface BatchUploadResponse {
  filename: string;
  sheets: BatchSheetInfo[];
}

export interface BatchRunConfig {
  filename: string;
  query_col: string;
  start_row: number;
  end_row: number;
  content_col: string;
  tool_col: string;
  model: string;
  prompt_version_id?: string | null;
  sheet_name?: string | null;
}

export interface BatchResult {
  row: number;
  query: string;
  content: string;
  tool_calls: string;
  status: 'done' | 'error' | 'skipped' | 'running';
}

export interface BatchTask {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'cancelled' | 'error';
  total: number;
  completed: number;
  current_query?: string;
  results: BatchResult[];
  created_at: string;
  error?: string;
}

export interface UserProfile {
  portfolio?: string;
  risk_preference?: string;
  investment_habits?: string;
  user_persona?: string;
  custom_notes?: string;
}

export interface SSECallbacks {
  onToken?: (content: string) => void;
  onThinking?: (content: string) => void;
  onToolCall?: (name: string, args: string) => void;
  onToolCallChunk?: (argsDelta: string) => void;
  onToolResult?: (name: string, content: string) => void;
  onDone?: () => void;
  onError?: (msg: string) => void;
  onInterrupt?: (actions: unknown[], configs: unknown) => void;
  onSubagentCall?: (name: string, task: string, subagentId?: number) => void;
  onSubagentCallChunk?: (argsDelta: string) => void;
  onSubagentStart?: (name: string, subagentId?: number) => void;
  onSubagentToken?: (content: string, agent: string, subagentId?: number) => void;
  onSubagentThinking?: (content: string, agent: string, subagentId?: number) => void;
  onSubagentToolCall?: (name: string, args: string, agent: string, subagentId?: number) => void;
  onSubagentToolChunk?: (argsDelta: string) => void;
  onSubagentToolResult?: (name: string, content: string, agent: string, subagentId?: number) => void;
  onSubagentEnd?: (name: string, result: string, subagentId?: number) => void;
}

export interface ChatOptions {
  model?: string;
  capabilities?: string[];
  plan_mode?: boolean;
}

export interface SchedulerTask {
  id: string;
  name: string;
  type: 'script' | 'agent';
  schedule_type: 'once' | 'cron' | 'interval';
  enabled: boolean;
  created_at: string;
  next_run?: string;
  last_run?: string;
  runs?: SchedulerRun[];
}

export interface SchedulerRun {
  id: string;
  started_at: string;
  ended_at?: string;
  status: 'running' | 'success' | 'error';
  steps?: SchedulerStep[];
}

export interface SchedulerStep {
  type: string;
  content: string;
  ts: string;
}

export interface ServiceConfig {
  id: string;
  name: string;
  description?: string;
  model?: string;
  capabilities?: string[];
  allowed_docs?: string[];
  allowed_scripts?: string[];
  system_prompt?: string;
  system_prompt_version_id?: string;
  user_profile_version_id?: string;
  published?: boolean;
  created_at?: string;
  research_tools?: boolean;
  wechat_channel?: {
    enabled: boolean;
    expires_at?: string;
    max_sessions?: number;
  };
}

export interface ServiceKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at?: string;
}

export interface WeChatSession {
  session_id: string;
  conversation_id: string;
  created_at: string;
  last_active_at: string;
}

export interface WeChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
}

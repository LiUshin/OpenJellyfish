import type {
  AuthResult,
  User,
  Conversation,
  ConversationDetail,
  FileItem,
  ModelsResponse,
  SystemPromptResponse,
  PromptVersion,
  SubagentListResponse,
  SubagentConfig,
  BatchUploadResponse,
  BatchRunConfig,
  BatchTask,
  UserProfile,
  SSECallbacks,
  ChatOptions,
} from '../types';

const BASE = '/api';

export function getToken(): string {
  return localStorage.getItem('token') || '';
}

export function setToken(token: string): void {
  localStorage.setItem('token', token);
}

export function clearToken(): void {
  localStorage.removeItem('token');
}

export async function request<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${getToken()}`,
  };

  if (body && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const config: RequestInit = {
    method,
    headers,
    ...options,
  };

  if (body) {
    config.body = body instanceof FormData ? body : JSON.stringify(body);
  }

  const res = await fetch(`${BASE}${path}`, config);

  if (res.status === 401) {
    const err401 = await res.json().catch(() => ({ detail: '认证失败' }));
    if (getToken()) {
      clearToken();
      window.location.reload();
    }
    throw new Error(err401.detail || '认证失败，请重新登录');
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '请求失败' }));
    throw new Error(err.detail || '请求失败');
  }

  return res.json();
}

// ===== Auth =====

export async function register(
  username: string,
  password: string,
  regKey: string,
): Promise<AuthResult> {
  return request('POST', '/auth/register', { username, password, reg_key: regKey });
}

export async function login(username: string, password: string): Promise<AuthResult> {
  return request('POST', '/auth/login', { username, password });
}

export async function getMe(): Promise<User> {
  return request('GET', '/auth/me');
}

// ===== Conversations =====

export async function listConversations(): Promise<Conversation[]> {
  return request('GET', '/conversations');
}

export async function createConversation(title = '新对话'): Promise<Conversation> {
  return request('POST', '/conversations', { title });
}

export async function getConversation(convId: string): Promise<ConversationDetail> {
  return request('GET', `/conversations/${convId}`);
}

export async function deleteConversation(convId: string): Promise<void> {
  return request('DELETE', `/conversations/${convId}`);
}

// ===== Chat (SSE) =====

let _currentAbortController: AbortController | null = null;

export function abortStream(): void {
  if (_currentAbortController) {
    _currentAbortController.abort();
    _currentAbortController = null;
  }
}

function handleSSEStream(res: Response, callbacks: SSECallbacks): void {
  const {
    onToken, onThinking, onToolCall, onToolCallChunk, onToolResult,
    onDone, onError, onInterrupt, onAutoApprove,
    onSubagentCall, onSubagentCallChunk, onSubagentStart, onSubagentToken,
    onSubagentThinking, onSubagentToolCall, onSubagentToolChunk,
    onSubagentToolResult, onSubagentEnd,
  } = callbacks;

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  (async () => {
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop()!;

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const dataStr = line.slice(6).trim();
          if (!dataStr) continue;

          try {
            const data = JSON.parse(dataStr);
            switch (data.type) {
              case 'token':              onToken?.(data.content); break;
              case 'thinking':           onThinking?.(data.content); break;
              case 'interrupt':          onInterrupt?.(data.actions, data.configs); _currentAbortController = null; return;
              case 'auto_approve':       onAutoApprove?.(data.count, data.actions); break;
              case 'tool_call':          onToolCall?.(data.name, data.args); break;
              case 'tool_call_chunk':    onToolCallChunk?.(data.args_delta); break;
              case 'tool_result':        onToolResult?.(data.name, data.content); break;
              case 'subagent_call':      onSubagentCall?.(data.name, data.task, data.subagent_id); break;
              case 'subagent_call_chunk': onSubagentCallChunk?.(data.args_delta); break;
              case 'subagent_start':     onSubagentStart?.(data.name, data.subagent_id); break;
              case 'subagent_token':     onSubagentToken?.(data.content, data.agent, data.subagent_id); break;
              case 'subagent_thinking':  onSubagentThinking?.(data.content, data.agent, data.subagent_id); break;
              case 'subagent_tool_call': onSubagentToolCall?.(data.name, data.args, data.agent, data.subagent_id); break;
              case 'subagent_tool_chunk': onSubagentToolChunk?.(data.args_delta); break;
              case 'subagent_tool_result': onSubagentToolResult?.(data.name, data.content, data.agent, data.subagent_id); break;
              case 'subagent_end':       onSubagentEnd?.(data.name, data.result, data.subagent_id); break;
              case 'done':              onDone?.(); _currentAbortController = null; return;
              case 'error':             onError?.(data.content); _currentAbortController = null; return;
            }
          } catch { /* ignore JSON parse error */ }
        }
      }

      if (buffer.startsWith('data: ')) {
        try {
          const data = JSON.parse(buffer.slice(6).trim());
          if (data.type === 'token') onToken?.(data.content);
        } catch { /* ignore */ }
      }

      onDone?.();
    } catch (e: unknown) {
      if (e instanceof Error && e.name === 'AbortError') return;
      onError?.(e instanceof Error ? e.message : '连接中断');
    } finally {
      _currentAbortController = null;
    }
  })();
}

export function streamChat(
  conversationId: string,
  message: string | unknown[],
  callbacks: SSECallbacks,
  options: ChatOptions = {},
): void {
  abortStream();

  const controller = new AbortController();
  _currentAbortController = controller;

  const body: Record<string, unknown> = { conversation_id: conversationId, message };
  if (options.model) body.model = options.model;
  if (options.capabilities) body.capabilities = options.capabilities;
  if (options.plan_mode) body.plan_mode = true;
  if (options.yolo) body.yolo = true;

  fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${getToken()}`,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '请求失败' }));
        callbacks.onError?.(err.detail || '请求失败');
        return;
      }
      handleSSEStream(res, callbacks);
    })
    .catch((err) => {
      if (err.name === 'AbortError') return;
      callbacks.onError?.(err.message);
    });
}

export async function stopChat(conversationId: string): Promise<void> {
  abortStream();
  await request('POST', '/chat/stop', { conversation_id: conversationId }).catch(() => {});
}

export interface StreamingStatusResult {
  streaming: string[];
  interrupted: string[];
}

export async function getStreamingStatus(): Promise<StreamingStatusResult> {
  const res = await request<StreamingStatusResult>('GET', '/chat/streaming-status');
  return { streaming: res.streaming ?? [], interrupted: res.interrupted ?? [] };
}

export interface InterruptStateResult {
  has_interrupt: boolean;
  actions?: unknown[];
  configs?: unknown;
}

export async function getInterruptState(conversationId: string): Promise<InterruptStateResult> {
  return request<InterruptStateResult>('GET', `/chat/interrupt/${conversationId}`);
}

export function resumeChat(
  conversationId: string,
  decisions: unknown[],
  callbacks: SSECallbacks,
  options: ChatOptions = {},
): void {
  abortStream();

  const controller = new AbortController();
  _currentAbortController = controller;

  const body: Record<string, unknown> = { conversation_id: conversationId, decisions };
  if (options.model) body.model = options.model;
  if (options.capabilities) body.capabilities = options.capabilities;
  if (options.yolo) body.yolo = true;

  fetch(`${BASE}/chat/resume`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${getToken()}`,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '请求失败' }));
        callbacks.onError?.(err.detail || '请求失败');
        return;
      }
      handleSSEStream(res, callbacks);
    })
    .catch((err) => {
      if (err.name === 'AbortError') return;
      callbacks.onError?.(err.message);
    });
}

// ===== Files =====

export async function listFiles(path = '/'): Promise<FileItem[]> {
  return request('GET', `/files?path=${encodeURIComponent(path)}`);
}

export async function readFile(path: string): Promise<{ content: string }> {
  return request('GET', `/files/read?path=${encodeURIComponent(path)}`);
}

export async function writeFile(path: string, content: string): Promise<void> {
  return request('POST', '/files/write', { path, content });
}

export async function editFile(path: string, oldString: string, newString: string): Promise<void> {
  return request('PUT', '/files/edit', { path, old_string: oldString, new_string: newString });
}

export async function deleteFile(path: string): Promise<void> {
  return request('DELETE', `/files?path=${encodeURIComponent(path)}`);
}

export async function moveFile(source: string, destination: string): Promise<void> {
  return request('POST', '/files/move', { source, destination });
}

export async function uploadFiles(path: string, files: File[], keepStructure = false): Promise<void> {
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file);
  }
  const qs = `path=${encodeURIComponent(path)}${keepStructure ? '&keep_structure=true' : ''}`;
  return request('POST', `/files/upload?${qs}`, formData);
}

export async function downloadFile(path: string): Promise<Response> {
  const res = await fetch(`${BASE}/files/download?path=${encodeURIComponent(path)}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) throw new Error('下载失败');
  return res;
}

export function mediaUrl(path: string): string {
  return `${BASE}/files/media?path=${encodeURIComponent(path)}&token=${encodeURIComponent(getToken())}`;
}

export function attachmentUrl(convId: string, path: string): string {
  return `${BASE}/conversations/${convId}/attachments/${encodeURIComponent(path)}`;
}

// ===== System Prompt =====

export async function getSystemPrompt(): Promise<SystemPromptResponse> {
  return request('GET', '/system-prompt');
}

export async function updateSystemPrompt(prompt: string): Promise<void> {
  return request('PUT', '/system-prompt', { prompt });
}

export async function resetSystemPrompt(): Promise<{ prompt: string }> {
  return request('DELETE', '/system-prompt');
}

export async function listPromptVersions(): Promise<PromptVersion[]> {
  return request('GET', '/system-prompt/versions');
}

export async function savePromptVersion(
  content: string,
  label = '',
  note = '',
): Promise<void> {
  return request('POST', '/system-prompt/versions', { content, label, note });
}

export async function getPromptVersion(versionId: string): Promise<PromptVersion & { content: string }> {
  return request('GET', `/system-prompt/versions/${versionId}`);
}

export async function updatePromptVersionMeta(
  versionId: string,
  label?: string,
  note?: string,
): Promise<void> {
  return request('PUT', `/system-prompt/versions/${versionId}`, { label, note });
}

export async function deletePromptVersion(versionId: string): Promise<void> {
  return request('DELETE', `/system-prompt/versions/${versionId}`);
}

export async function rollbackPromptVersion(versionId: string): Promise<{ prompt: string }> {
  return request('POST', `/system-prompt/versions/${versionId}/rollback`);
}

// ===== User Profile =====

export async function getUserProfile(): Promise<{ profile: UserProfile }> {
  return request('GET', '/user-profile');
}

export async function updateUserProfile(profile: UserProfile): Promise<void> {
  return request('PUT', '/user-profile', profile);
}

export async function listProfileVersions(): Promise<PromptVersion[]> {
  return request('GET', '/user-profile/versions');
}

export async function getProfileVersion(versionId: string): Promise<{ id: string; content: string; label: string; note: string }> {
  return request('GET', `/user-profile/versions/${versionId}`);
}

export async function deleteProfileVersion(versionId: string): Promise<void> {
  return request('DELETE', `/user-profile/versions/${versionId}`);
}

export async function rollbackProfileVersion(versionId: string): Promise<{ content: string }> {
  return request('POST', `/user-profile/versions/${versionId}/rollback`);
}

// ===== Scripts =====

export async function runScript(
  scriptPath: string,
  args: string[] | null = null,
  inputData: string | null = null,
  timeout = 30,
): Promise<unknown> {
  return request('POST', '/scripts/run', {
    script_path: scriptPath,
    args,
    input_data: inputData,
    timeout,
  });
}

// ===== Models =====

export async function getModels(): Promise<ModelsResponse> {
  return request('GET', '/models');
}

// ===== Audio =====

export async function transcribeAudio(
  audioBlob: Blob,
  filename = 'recording.webm',
): Promise<{ text: string }> {
  const formData = new FormData();
  formData.append('file', audioBlob, filename);

  const res = await fetch(`${BASE}/audio/transcribe`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getToken()}` },
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || '语音识别失败');
  }
  return res.json();
}

// ===== Subagents =====

export async function listSubagents(): Promise<SubagentListResponse> {
  return request('GET', '/subagents');
}

export async function addSubagent(config: Partial<SubagentConfig>): Promise<void> {
  return request('POST', '/subagents', config);
}

export async function getSubagent(id: string): Promise<SubagentConfig> {
  return request('GET', `/subagents/${id}`);
}

export async function updateSubagent(id: string, updates: Partial<SubagentConfig>): Promise<void> {
  return request('PUT', `/subagents/${id}`, updates);
}

export async function deleteSubagent(id: string): Promise<void> {
  return request('DELETE', `/subagents/${id}`);
}

// ===== Capability Prompts =====

export interface CapabilityPromptItem {
  key: string;
  default: string;
  custom: string | null;
}

export async function getCapabilityPrompts(): Promise<{ prompts: CapabilityPromptItem[] }> {
  return request('GET', '/capability-prompts');
}

export async function updateCapabilityPrompt(key: string, text: string): Promise<void> {
  return request('PUT', `/capability-prompts/${key}`, { text });
}

export async function resetCapabilityPrompt(key: string): Promise<void> {
  return request('DELETE', `/capability-prompts/${key}`);
}

// ===== Soul Config =====

export interface SoulConfig {
  memory_enabled: boolean;
  include_consumer_conversations: boolean;
  max_recent_messages: number;
  memory_subagent_enabled: boolean;
  soul_edit_enabled: boolean;
}

export async function getSoulConfig(): Promise<SoulConfig> {
  return request('GET', '/soul/config');
}

export async function updateSoulConfig(updates: Partial<SoulConfig>): Promise<{ success: boolean; config: SoulConfig }> {
  return request('PUT', '/soul/config', updates);
}

// ===== Python Packages =====

export interface PackageInfo {
  name: string;
  version: string;
}

export async function listPackages(): Promise<{ packages: PackageInfo[]; venv_ready: boolean }> {
  return request('GET', '/packages');
}

export async function initVenv(): Promise<{ success: boolean }> {
  return request('POST', '/packages/init');
}

export async function installPackage(pkg: string): Promise<{ success: boolean; output: string }> {
  return request('POST', '/packages/install', { package: pkg });
}

export async function uninstallPackage(pkg: string): Promise<{ success: boolean; output: string }> {
  return request('POST', '/packages/uninstall', { package: pkg });
}

// ===== Batch Execution =====

export async function uploadBatchExcel(file: File): Promise<BatchUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${BASE}/batch/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getToken()}` },
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || '上传失败');
  }
  return res.json();
}

export async function startBatchRun(config: BatchRunConfig): Promise<{ task_id: string; total: number }> {
  return request('POST', '/batch/run', config);
}

export async function listBatchTasks(): Promise<BatchTask[]> {
  return request('GET', '/batch/tasks');
}

export async function getBatchTask(taskId: string): Promise<BatchTask> {
  return request('GET', `/batch/tasks/${taskId}`);
}

export async function cancelBatchTask(taskId: string): Promise<void> {
  return request('POST', `/batch/tasks/${taskId}/cancel`);
}

export function batchDownloadUrl(taskId: string): string {
  return `${BASE}/batch/tasks/${taskId}/download?token=${encodeURIComponent(getToken())}`;
}

// ===== Scheduler =====

export async function listSchedulerTasks(): Promise<unknown[]> {
  return request('GET', '/scheduler');
}

export async function getSchedulerTask(taskId: string): Promise<unknown> {
  return request('GET', `/scheduler/${taskId}`);
}

export async function createSchedulerTask(config: unknown): Promise<unknown> {
  return request('POST', '/scheduler', config);
}

export async function updateSchedulerTask(taskId: string, config: unknown): Promise<void> {
  return request('PUT', `/scheduler/${taskId}`, config);
}

export async function deleteSchedulerTask(taskId: string): Promise<void> {
  return request('DELETE', `/scheduler/${taskId}`);
}

export async function getSchedulerRuns(taskId: string): Promise<unknown[]> {
  return request('GET', `/scheduler/${taskId}/runs`);
}

export async function runSchedulerTaskNow(taskId: string): Promise<void> {
  return request('POST', `/scheduler/${taskId}/run-now`);
}

// ===== Services =====

export async function listServices(): Promise<unknown[]> {
  return request('GET', '/services');
}

export async function getService(serviceId: string): Promise<unknown> {
  return request('GET', `/services/${serviceId}`);
}

export async function createService(config: unknown): Promise<unknown> {
  return request('POST', '/services', config);
}

export async function updateService(serviceId: string, config: unknown): Promise<void> {
  return request('PUT', `/services/${serviceId}`, config);
}

export async function deleteService(serviceId: string): Promise<void> {
  return request('DELETE', `/services/${serviceId}`);
}

export async function listServiceKeys(serviceId: string): Promise<unknown[]> {
  return request('GET', `/services/${serviceId}/keys`);
}

export async function createServiceKey(
  serviceId: string,
  name: string,
): Promise<{ key: string }> {
  return request('POST', `/services/${serviceId}/keys`, { name });
}

export async function deleteServiceKey(
  serviceId: string,
  keyId: string,
): Promise<void> {
  return request('DELETE', `/services/${serviceId}/keys/${keyId}`);
}

// 使用情况：consumer 会话历史 + API 调用记录

export interface ServiceConvSummary {
  id: string;
  title: string;
  source: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ServiceConvDetail extends ServiceConvSummary {
  messages: Array<{
    role: string;
    content: string;
    timestamp?: string;
    tool_calls?: unknown;
    attachments?: unknown;
    blocks?: unknown;
  }>;
}

export interface ServiceUsageRecord {
  ts: string;
  channel: 'web' | 'api' | 'wechat' | string;
  key_id: string;
  conv_id: string;
  endpoint: string;
  status_code: number;
  latency_ms: number;
  ok: boolean;
}

export async function listServiceConversations(
  serviceId: string,
): Promise<ServiceConvSummary[]> {
  return request('GET', `/services/${serviceId}/conversations`);
}

export async function getServiceConversation(
  serviceId: string,
  convId: string,
): Promise<ServiceConvDetail> {
  return request('GET', `/services/${serviceId}/conversations/${convId}`);
}

export async function deleteServiceConversation(
  serviceId: string,
  convId: string,
): Promise<void> {
  return request('DELETE', `/services/${serviceId}/conversations/${convId}`);
}

export async function listServiceUsage(
  serviceId: string,
  opts?: { limit?: number; channel?: 'web' | 'api' | 'wechat' },
): Promise<{ records: ServiceUsageRecord[]; limit: number; channel: string | null }> {
  const params = new URLSearchParams();
  if (opts?.limit) params.set('limit', String(opts.limit));
  if (opts?.channel) params.set('channel', opts.channel);
  const qs = params.toString();
  return request('GET', `/services/${serviceId}/usage${qs ? `?${qs}` : ''}`);
}

// ===== Inbox =====

export interface InboxMessage {
  id: string;
  service_id: string;
  service_name: string;
  conversation_id: string;
  wechat_session_id?: string;
  wechat_user_id?: string;
  message: string;
  timestamp: string;
  status: 'unread' | 'read' | 'handled';
  handled_by: 'agent' | 'manual' | null;
  agent_response: string | null;
}

export async function listInbox(status?: string): Promise<{ messages: InboxMessage[]; unread_count: number }> {
  const params = status ? `?status=${status}` : '';
  return request('GET', `/inbox${params}`);
}

export async function getInboxUnreadCount(): Promise<{ count: number }> {
  return request('GET', '/inbox/unread-count');
}

export async function getInboxMessage(msgId: string): Promise<InboxMessage> {
  return request('GET', `/inbox/${msgId}`);
}

export async function updateInboxStatus(msgId: string, status: string): Promise<InboxMessage> {
  return request('PUT', `/inbox/${msgId}`, { status });
}

export async function deleteInboxMessage(msgId: string): Promise<void> {
  return request('DELETE', `/inbox/${msgId}`);
}

// ===== API Keys (per-user) =====

export interface ApiKeysStatus {
  has_llm: boolean;
  has_openai: boolean;
  has_anthropic: boolean;
}

export interface ApiKeysMasked {
  openai_api_key: string;
  openai_api_key_configured: boolean;
  openai_base_url: string;
  anthropic_api_key: string;
  anthropic_api_key_configured: boolean;
  anthropic_base_url: string;
  tavily_api_key: string;
  tavily_api_key_configured: boolean;
  [key: string]: string | boolean;
}

export interface ApiKeysTestResult {
  results: Record<string, { ok: boolean; status?: number; error?: string }>;
}

export async function getApiKeys(): Promise<ApiKeysMasked> {
  return request('GET', '/settings/api-keys');
}

export async function updateApiKeys(keys: Record<string, string>): Promise<{ success: boolean; keys: ApiKeysMasked }> {
  return request('PUT', '/settings/api-keys', keys);
}

export async function testApiKeys(provider: string): Promise<ApiKeysTestResult> {
  return request('POST', '/settings/api-keys/test', { provider });
}

export async function getApiKeysStatus(): Promise<ApiKeysStatus> {
  return request('GET', '/settings/api-keys/status');
}

// ===== Preferences =====

export interface UserPreferences {
  tz_offset_hours: number;
}

export async function getPreferences(): Promise<UserPreferences> {
  return request('GET', '/preferences');
}

export async function updatePreferences(data: Partial<UserPreferences>): Promise<UserPreferences> {
  return request('PUT', '/preferences', data);
}

export async function getServerTime(): Promise<{ server_time: string }> {
  return request('GET', '/server-time');
}

// ===== Backup & Restore (per-user) =====

export interface BackupModule {
  id: string;
  label: string;
}

export interface BackupModulesResp {
  modules: BackupModule[];
  default_selected: string[];
}

export interface BackupPreviewResp {
  modules: Record<string, { file_count: number; total_bytes: number }>;
  total_file_count: number;
  total_uncompressed_bytes: number;
  selection: string[];
}

export interface BackupImportResp {
  ok: boolean;
  mode: 'merge' | 'overwrite';
  files_written: number;
  files_skipped: number;
  api_keys_imported: number;
  snapshot_path: string | null;
  warnings: string[];
}

export async function listBackupModules(): Promise<BackupModulesResp> {
  return request('GET', '/backup/modules');
}

export async function previewBackup(opts: {
  modules: string[];
  includeMedia: boolean;
  includeApiKeys: boolean;
}): Promise<BackupPreviewResp> {
  const fd = new FormData();
  fd.append('modules', opts.modules.join(','));
  fd.append('include_media', String(opts.includeMedia));
  fd.append('include_api_keys', String(opts.includeApiKeys));
  return request('POST', '/backup/preview', fd);
}

/**
 * Trigger a streaming download of the user backup ZIP.
 * Returns the suggested filename + size in bytes.
 */
export async function downloadBackup(opts: {
  modules: string[];
  includeMedia: boolean;
  includeApiKeys: boolean;
}): Promise<{ filename: string; sizeBytes: number; fileCount: number }> {
  const fd = new FormData();
  fd.append('modules', opts.modules.join(','));
  fd.append('include_media', String(opts.includeMedia));
  fd.append('include_api_keys', String(opts.includeApiKeys));

  const res = await fetch(`${BASE}/backup/export`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getToken()}` },
    body: fd,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); detail = j.detail || detail; } catch { /* noop */ }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') || '';
  const m = /filename="([^"]+)"/.exec(cd);
  const filename = m ? m[1] : `jellyfishbot-backup-${Date.now()}.zip`;
  const fileCount = parseInt(res.headers.get('X-Backup-File-Count') || '0', 10);

  // Trigger browser save dialog.
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return { filename, sizeBytes: blob.size, fileCount };
}

export async function importBackup(opts: {
  file: File;
  mode: 'merge' | 'overwrite';
  password?: string;
  modules?: string[];
}): Promise<BackupImportResp> {
  const fd = new FormData();
  fd.append('file', opts.file);
  fd.append('mode', opts.mode);
  if (opts.password) fd.append('password', opts.password);
  if (opts.modules && opts.modules.length) fd.append('modules', opts.modules.join(','));
  return request('POST', '/backup/import', fd);
}

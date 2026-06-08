/**
 * Consumer-side API client for service-chat.html.
 *
 * 故意与 admin 的 src/services/api.ts 解耦——consumer 用的是 service API key
 * (sk-svc-…)，鉴权头与 endpoint 路径都不同，避免共享 token 状态导致的串台。
 */

const API_BASE = window.location.origin;

export interface ServiceChatRequest {
  conversation_id: string;
  message: string | unknown[];
}

export function getStoredKey(serviceId: string): string {
  return localStorage.getItem(`svc_key_${serviceId}`) || '';
}

export function setStoredKey(serviceId: string, key: string): void {
  localStorage.setItem(`svc_key_${serviceId}`, key);
}

export function clearStoredKey(serviceId: string): void {
  localStorage.removeItem(`svc_key_${serviceId}`);
}

/**
 * 把 ?key=xxx 从 URL 提取并写入 localStorage，再用 history.replaceState 清掉，
 * 避免 referer / 浏览器历史二次曝光。
 */
export function consumeKeyFromUrl(serviceId: string): string | null {
  const params = new URLSearchParams(window.location.search);
  const key = params.get('key');
  if (!key) return null;
  setStoredKey(serviceId, key);
  params.delete('key');
  const search = params.toString();
  const cleanUrl =
    window.location.pathname + (search ? '?' + search : '') + window.location.hash;
  window.history.replaceState({}, '', cleanUrl);
  return key;
}

function authHeaders(apiKey: string, json = false): HeadersInit {
  const h: Record<string, string> = { Authorization: `Bearer ${apiKey}` };
  if (json) h['Content-Type'] = 'application/json';
  return h;
}

export async function createConversation(apiKey: string, title = ''): Promise<{ id: string }> {
  const res = await fetch(`${API_BASE}/api/v1/conversations`, {
    method: 'POST',
    headers: authHeaders(apiKey, true),
    body: JSON.stringify({ title }),
  });
  if (res.status === 401) throw new AuthError('API Key 无效，请重新输入');
  if (!res.ok) throw new Error(`创建会话失败: ${res.status}`);
  return res.json();
}

export interface ChatStreamHandle {
  reader: ReadableStreamDefaultReader<Uint8Array>;
  abort: () => void;
}

export async function openChatStream(
  apiKey: string,
  body: ServiceChatRequest,
  signal?: AbortSignal,
): Promise<Response> {
  const res = await fetch(`${API_BASE}/api/v1/chat`, {
    method: 'POST',
    headers: authHeaders(apiKey, true),
    body: JSON.stringify(body),
    signal,
  });
  if (res.status === 401) throw new AuthError('API Key 无效，请重新输入');
  return res;
}

export interface GeneratedFile {
  path: string;
  size: number;
}

// ── 本地会话列表持久化 ───────────────────────────────────────────────
// service key 是共享的、后端不按消费者隔离，无法安全地「列出本人的会话」。
// 因此把「本浏览器创建过的会话」存在 localStorage，刷新不丢、可管理多个会话。
// 会话内容（消息）仍存后端，切换时按 conv_id 拉取。

export interface ConvMeta {
  id: string;
  title: string;
  updatedAt: string;
}

interface ConvStore {
  items: ConvMeta[];
  activeId: string | null;
}

const CONV_STORE_PREFIX = 'svc_convs_';

export function loadConvStore(serviceId: string): ConvStore {
  try {
    const raw = localStorage.getItem(CONV_STORE_PREFIX + serviceId);
    if (raw) {
      const p = JSON.parse(raw);
      if (p && Array.isArray(p.items)) {
        return { items: p.items as ConvMeta[], activeId: p.activeId ?? null };
      }
    }
  } catch {
    /* ignore corrupt store */
  }
  return { items: [], activeId: null };
}

export function saveConvStore(serviceId: string, items: ConvMeta[], activeId: string | null): void {
  try {
    localStorage.setItem(
      CONV_STORE_PREFIX + serviceId,
      JSON.stringify({ items, activeId }),
    );
  } catch {
    /* quota / private mode — 持久化失败不致命 */
  }
}

// ── 拉取单个会话（含历史消息）────────────────────────────────────────
export interface ConsumerMessage {
  role: string;
  content: string;
  timestamp?: string;
  tool_calls?: { name?: string; args?: string; result?: string }[];
  blocks?: unknown[];
}

export interface ConsumerConversation {
  id: string;
  title: string;
  messages: ConsumerMessage[];
  created_at?: string;
  updated_at?: string;
  message_count?: number;
}

/** 按 conv_id 拉取会话历史；404（已被删/不存在）返回 null 由调用方清理本地记录。 */
export async function getConversation(
  apiKey: string,
  convId: string,
): Promise<ConsumerConversation | null> {
  const res = await fetch(
    `${API_BASE}/api/v1/conversations/${encodeURIComponent(convId)}`,
    { headers: authHeaders(apiKey) },
  );
  if (res.status === 401) throw new AuthError('API Key 无效，请重新输入');
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`加载会话失败: ${res.status}`);
  return res.json();
}

/**
 * 取本会话的短期媒体 token。前端用它构造 <img>/<a download>/<iframe> 的 URL，
 * 而不把 sk-svc- 主 key 暴露在 URL 里。token 绑定单一 (service, conversation)，
 * 有过期时间（后端默认 6h），失效后重新取即可。
 */
export async function getMediaToken(
  apiKey: string,
  convId: string,
): Promise<string> {
  const res = await fetch(
    `${API_BASE}/api/v1/conversations/${encodeURIComponent(convId)}/media-token`,
    { headers: authHeaders(apiKey) },
  );
  if (res.status === 401) throw new AuthError('API Key 无效，请重新输入');
  if (!res.ok) throw new Error(`获取媒体 token 失败: ${res.status}`);
  const data = await res.json();
  return data.token as string;
}

/** 列出本会话 generated/ 下的文件（用主 key 走 Authorization header）。 */
export async function listGeneratedFiles(
  apiKey: string,
  convId: string,
): Promise<GeneratedFile[]> {
  const res = await fetch(
    `${API_BASE}/api/v1/conversations/${encodeURIComponent(convId)}/files`,
    { headers: authHeaders(apiKey) },
  );
  if (res.status === 401) throw new AuthError('API Key 无效，请重新输入');
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`列出文件失败: ${res.status}`);
  return res.json();
}

/**
 * 构造消费者端的鉴权媒体 URL（基于短期 media token）。
 *
 * agent 在回复里写 `<<FILE:/generated/images/foo.png>>`，markdown.ts 把路径丢给
 * 注入的 mediaUrl builder。consumer 走会话级文件端点：
 *     GET /api/v1/conversations/{conv_id}/files/{file_path}?token=...[&download=1]
 *
 * 浏览器 <img src> 不支持 Authorization header，故用 query 携带 token。相比直接放
 * sk-svc- 主 key：token 仅绑定单一会话的文件、且会过期，泄露面更小。token 为空时
 * 退化为带 key（兜底，理论上拿到 token 前不会渲染媒体）。
 */
export function buildConsumerMediaUrl(
  token: string,
  convId: string | null,
  filePath: string,
  opts?: { download?: boolean },
): string {
  if (!convId) return filePath;
  const stripped = filePath.replace(/^\/+/, '').replace(/^generated\//, '');
  const encoded = stripped.split('/').map(encodeURIComponent).join('/');
  const params = new URLSearchParams();
  if (token) params.set('token', token);
  if (opts?.download) params.set('download', '1');
  const qs = params.toString();
  return `${API_BASE}/api/v1/conversations/${encodeURIComponent(convId)}/files/${encoded}${qs ? '?' + qs : ''}`;
}

export class AuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AuthError';
  }
}

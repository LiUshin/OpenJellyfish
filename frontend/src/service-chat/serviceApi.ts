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

/**
 * 构造消费者端的鉴权媒体 URL。
 *
 * agent 在回复里写 `<<FILE:/generated/images/foo.png>>`，markdown.ts 会把这个
 * 路径丢给注入的 mediaUrl builder。consumer 走的是会话级文件端点：
 *     GET /api/v1/conversations/{conv_id}/files/{file_path}?key=...
 *
 * 注意：浏览器 <img src> 不支持 Authorization header，所以用 query 参数携带 key。
 * 这是一个权衡——key 会出现在浏览器请求 URL 里，但 service API key 本来就是
 * 客户端持有（localStorage），且只能访问当前 service / conversation 范围内的文件。
 */
export function buildConsumerMediaUrl(
  apiKey: string,
  convId: string | null,
  filePath: string,
): string {
  if (!convId) return filePath;
  const stripped = filePath.replace(/^\/+/, '').replace(/^generated\//, '');
  const encoded = stripped.split('/').map(encodeURIComponent).join('/');
  return `${API_BASE}/api/v1/conversations/${encodeURIComponent(convId)}/files/${encoded}?key=${encodeURIComponent(apiKey)}`;
}

export class AuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AuthError';
  }
}

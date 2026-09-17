import type { StreamBlock } from '../pages/Chat/types';
import { request, getToken } from './api';

export interface RuntimeCapabilities {
  enabled: boolean;
  available: boolean;
  can_manage_connections: boolean;
  reason: string | null;
  access_mode: string;
  execution_backend: string;
}
export interface RuntimeProfile {
  id: string;
  name: string;
  runtime: 'codex' | 'cursor';
  status: string;
  worker?: { status: 'warming' | 'ready' | 'busy' | 'sleeping' | 'error'; error?: string };
  capabilities?: { web_search?: boolean; image_generation?: boolean; image_input?: boolean; file_input?: boolean };
  models: { id: string; name: string }[];
  can_manage: boolean;
  source: 'personal' | 'owner_shared';
  recovery_required: boolean;
  auth_generation: number;
  account?: { email?: string; planType?: string };
  login_id?: string | null;
}
export interface LoginAttempt {
  id: string;
  profile_id: string;
  status: string;
  expires_at: number;
  challenge: { url: string; user_code?: string; mode: string } | null;
}
export const capabilities = () => request<RuntimeCapabilities>('GET', '/runtime/capabilities');
export const profiles = () => request<RuntimeProfile[]>('GET', '/runtime/profiles');
export const createProfile = (name: string, runtime: 'codex' | 'cursor' = 'codex') => request<RuntimeProfile>('POST', '/runtime/profiles', { name, runtime });
export const login = (id: string, mode: 'chatgpt' | 'chatgptDeviceCode' | 'cursorBrowser') => request<LoginAttempt>('POST', `/runtime/profiles/${id}/login`, { mode });
export const loginStatus = (pid: string, lid: string) => request<LoginAttempt>('GET', `/runtime/profiles/${pid}/login/${lid}`);
export const cancelLogin = (pid: string, lid: string) => request('DELETE', `/runtime/profiles/${pid}/login/${lid}`);
export const probe = (pid: string) => request<RuntimeProfile>('POST', `/runtime/profiles/${pid}/probe`);
export const disconnect = (pid: string) => request<RuntimeProfile>('DELETE', `/runtime/profiles/${pid}/connection`);

export interface RuntimeGrant {
  id: string; actor_id: string; models: string[]; enabled: boolean;
  auth_generation: number; version: number;
}
export const admins = () => request<{ id: string; username: string }[]>('GET', '/runtime/admins');
export const grants = (pid: string) => request<RuntimeGrant[]>('GET', `/runtime/profiles/${pid}/grants`);
export const grant = (pid: string, actor_id: string, models: string[]) => request<RuntimeGrant>('PUT', `/runtime/profiles/${pid}/grants`, { actor_id, models });
export const revoke = (pid: string, gid: string) => request('DELETE', `/runtime/profiles/${pid}/grants/${gid}`);

export interface RuntimeChoice { runtime: 'deepagents' | 'codex' | 'cursor'; profile_id?: string; model?: string; image_mode?: 'off' | 'native' }
export const preferences = () => request<RuntimeChoice>('GET', '/runtime/preferences');
export const savePreferences = (choice: RuntimeChoice) => request<RuntimeChoice>('PUT', '/runtime/preferences', choice);
export interface RuntimeApproval {
  id: string; kind: string; command?: string; reason?: string;
  changes?: { path: string; diff?: string }[]; allowed: ('accept' | 'decline')[];
}
export interface RuntimeArtifact { id: string; name: string; mime: string; size: number; path: string }
export interface RuntimeInput { name: string; data_url: string }
export interface RuntimeRun {
  id: string; status: string; seq: number; message: string; output: string;
  blocks?: StreamBlock[];
  prepare_timings?: Record<string, number>;
  binding: RuntimeChoice; client_reused?: boolean; client_acquired_at?: number;
  attachments?: RuntimeArtifact[];
  pending: RuntimeApproval | null; artifacts: RuntimeArtifact[];
  created_at: number; started_at?: number; first_token_at?: number; finished_at?: number;
  error?: string;
}
export interface RuntimeSession { id: string; binding: RuntimeChoice; runs: RuntimeRun[]; artifacts: RuntimeArtifact[] }
export interface RuntimeEvent {
  seq: number; type: string;
  payload: { started_at?: number; client_acquired_at?: number; item_id?: string; text?: string; message?: string; approval?: RuntimeApproval; artifact?: RuntimeArtifact; name?: string; kind?: string; status?: string; command?: string };
}
export const terminal = (status: string) => ['completed', 'failed', 'cancelled'].includes(status);
export const session = (sid: string) => request<RuntimeSession>('GET', `/runtime/sessions/${sid}`);
export const turn = (conversation_id: string, request_id: string, message: string, model?: string, attachments: RuntimeInput[] = []) => request<RuntimeRun>('POST', '/runtime/turns', { conversation_id, request_id, message, model, attachments });
export const cancel = (rid: string) => request<RuntimeRun>('POST', `/runtime/runs/${rid}/cancel`);
export const approve = (rid: string, approval_id: string, decision: 'accept' | 'decline') => request('POST', `/runtime/runs/${rid}/approve`, { approval_id, decision });

export async function watchRun(rid: string, after: number, signal: AbortSignal, onEvent: (event: RuntimeEvent) => void) {
  const response = await fetch(`/api/runtime/runs/${rid}/events?after=${after}`, { headers: { Authorization: `Bearer ${getToken()}` }, signal });
  if (!response.ok || !response.body) throw new Error(`运行状态连接失败 (${response.status})`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finished = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end: number;
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        const line = frame.split('\n').find(l => l.startsWith('data: '));
        if (!line) continue;
        const event = JSON.parse(line.slice(6)) as RuntimeEvent;
        onEvent(event);
        if (terminal(event.type)) finished = true;
      }
    }
    if (!finished) throw new Error('运行连接中断，正在恢复');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function artifact(sid: string, aid: string, input = false) {
  const response = await fetch(`/api/runtime/sessions/${sid}/${input ? 'inputs' : 'artifacts'}/${aid}`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!response.ok) throw new Error('无法读取产物，文件可能已被修改');
  return response.blob();
}

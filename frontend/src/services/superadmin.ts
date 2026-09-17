import type { RuntimeCapabilities, RuntimeProfile, LoginAttempt, RuntimeGrant } from './runtime';

export function hostClient(key: string, onExpired: () => void) {
  async function request<T>(method: string, path: string, data?: unknown): Promise<T> {
    const response = await fetch(`/api/superadmin/runtime${path}`, {
      method, credentials: 'omit', cache: 'no-store',
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
    if (!response.ok) {
      if (response.status === 401) onExpired();
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === 'string' ? error.detail : `请求失败 (${response.status})`);
    }
    return response.json() as Promise<T>;
  }
  return {
    capabilities: () => request<RuntimeCapabilities>('GET', '/capabilities'),
    profiles: () => request<RuntimeProfile[]>('GET', '/profiles'),
    createProfile: (name: string, runtime: 'codex' | 'cursor' = 'codex') => request<RuntimeProfile>('POST', '/profiles', { name, runtime }),
    login: (id: string, mode: 'chatgpt' | 'chatgptDeviceCode' | 'cursorBrowser') => request<LoginAttempt>('POST', `/profiles/${id}/login`, { mode }),
    loginStatus: (pid: string, lid: string) => request<LoginAttempt>('GET', `/profiles/${pid}/login/${lid}`),
    cancelLogin: (pid: string, lid: string) => request('DELETE', `/profiles/${pid}/login/${lid}`),
    probe: (pid: string) => request<RuntimeProfile>('POST', `/profiles/${pid}/probe`),
    disconnect: (pid: string) => request<RuntimeProfile>('DELETE', `/profiles/${pid}/connection`),
    admins: () => request<{ id: string; username: string }[]>('GET', '/admins'),
    grants: (pid: string) => request<RuntimeGrant[]>('GET', `/profiles/${pid}/grants`),
    grant: (pid: string, actor_id: string, models: string[]) => request<RuntimeGrant>('PUT', `/profiles/${pid}/grants`, { actor_id, models }),
    revoke: (pid: string, gid: string) => request('DELETE', `/profiles/${pid}/grants/${gid}`),
  };
}
export type HostClient = ReturnType<typeof hostClient>;

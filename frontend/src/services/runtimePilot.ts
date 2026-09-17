import { getToken, request } from './api';

export interface Approval {
  id: string; kind: string; allowed: string[]; command?: string; reason?: string; changes?: unknown;
}
export interface Artifact {
  id: string; name: string; mime: string; size: number; native_image: boolean;
}
export interface Run {
  id: string; status: string; message: string; pending: Approval | null;
}
export interface Session {
  id: string; model: string | null; created_at: number; imports: string[];
  artifacts: Artifact[]; runs: string[];
}
export interface PilotEvent {
  seq: number; type: string; run_id: string; text?: string; message?: string;
  command?: string; kind?: string; status?: string; approval?: Approval;
  artifact?: Artifact;
}
export const pilotRequest = <T>(method: string, path: string, body?: unknown) =>
  request<T>(method, `/runtime-pilot${path}`, body);

export async function readEvents(sid: string, rid: string, after: number,
  onEvent: (event: PilotEvent) => void, signal: AbortSignal) {
  const res = await fetch(`/api/runtime-pilot/sessions/${sid}/runs/${rid}/events?after=${after}`, {
    headers: { Authorization: `Bearer ${getToken()}` }, signal,
  });
  if (!res.ok) throw new Error((await res.json()).detail || '事件读取失败');
  if (!res.body) throw new Error('浏览器未提供事件流');
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      let split: number;
      while ((split = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        for (const line of frame.split('\n')) {
          if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)) as PilotEvent);
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function artifactBlob(sid: string, aid: string): Promise<Blob> {
  const res = await fetch(`/api/runtime-pilot/sessions/${sid}/artifacts/${aid}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) throw new Error((await res.json()).detail || '产物读取失败');
  return res.blob();
}

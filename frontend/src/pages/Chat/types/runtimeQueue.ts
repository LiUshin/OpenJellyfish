import type { QueryQueueItem } from './queryQueue';

export type RuntimeQueuedTurn = QueryQueueItem & {
  model?: string;
  yolo: boolean;
  /** A POST may have reached the server; freeze its payload until reconciled. */
  attempted?: boolean;
};

export type RuntimeQueues = Record<string, RuntimeQueuedTurn[]>;

export function runtimeQueueStorageKey(actorId: string): string {
  return `jf-runtime-followups-v1:${actorId}`;
}

export function readRuntimeQueues(key: string): RuntimeQueues {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(key) || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const result: RuntimeQueues = {};
    for (const [conversationId, rawItems] of Object.entries(parsed).slice(0, 100)) {
      if (!Array.isArray(rawItems) || !conversationId) continue;
      const items = rawItems.slice(0, 50).filter((item): item is RuntimeQueuedTurn =>
        !!item && typeof item === 'object' &&
        typeof item.id === 'string' && /^[0-9a-f-]{36}$/i.test(item.id) &&
        typeof item.content === 'string' && item.content.length <= 32000 &&
        item.mode === 'queue' && typeof item.yolo === 'boolean' &&
        (item.model === undefined || (typeof item.model === 'string' && item.model.length <= 200)) &&
        (item.attempted === undefined || typeof item.attempted === 'boolean'),
      );
      if (items.length) result[conversationId] = items;
    }
    return result;
  } catch { return {}; }
}

export function writeRuntimeQueues(key: string, queues: RuntimeQueues): void {
  try {
    const nonempty = Object.fromEntries(Object.entries(queues).filter(([, items]) => items.length));
    if (Object.keys(nonempty).length) sessionStorage.setItem(key, JSON.stringify(nonempty));
    else sessionStorage.removeItem(key);
  } catch { /* storage may be unavailable; the in-memory queue still works */ }
}

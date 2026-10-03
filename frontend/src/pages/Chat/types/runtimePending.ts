/** One unresolved direct CLI POST per conversation. The request ID must survive
 * navigation and reloads so a retry cannot start a second execution. */
export interface RuntimePendingTurn {
  conversationId: string;
  requestId: string;
  text: string;
  model?: string;
  yolo: boolean;
  attachments: { name: string; dataUrl: string }[];
  kind: 'initial' | 'reply';
}

export type RuntimePendingTurns = Record<string, RuntimePendingTurn>;

export function runtimePendingStorageKey(actorId: string): string {
  return `jf-runtime-pending-v1:${actorId}`;
}

export function readRuntimePending(key: string): RuntimePendingTurns {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(key) || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const result: RuntimePendingTurns = {};
    for (const [conversationId, raw] of Object.entries(parsed)) {
      if (!raw || typeof raw !== 'object') continue;
      const item = raw as Partial<RuntimePendingTurn>;
      if (item.conversationId !== conversationId ||
          typeof item.requestId !== 'string' || !/^[0-9a-f-]{36}$/i.test(item.requestId) ||
          typeof item.text !== 'string' || item.text.length > 32000 ||
          (item.model !== undefined && (typeof item.model !== 'string' || item.model.length > 200)) ||
          typeof item.yolo !== 'boolean' ||
          (item.kind !== 'initial' && item.kind !== 'reply') ||
          !Array.isArray(item.attachments) || item.attachments.length > 5 ||
          !item.attachments.every(file => file && typeof file.name === 'string' &&
            typeof file.dataUrl === 'string' && file.dataUrl.startsWith('data:'))) continue;
      result[conversationId] = item as RuntimePendingTurn;
    }
    return result;
  } catch { return {}; }
}

/** Return false if the browser cannot durably record the request. Callers must
 * leave the composer intact and avoid the POST in that case. */
export function writeRuntimePending(key: string, pending: RuntimePendingTurns): boolean {
  try {
    if (Object.keys(pending).length) sessionStorage.setItem(key, JSON.stringify(pending));
    else sessionStorage.removeItem(key);
    return true;
  } catch { return false; }
}

export function pendingAccepted(item: RuntimePendingTurn, runs: { request_id?: string }[]): boolean {
  return runs.some(run => run.request_id === item.requestId);
}

/** A pending first turn can belong to an admission POST that is still in flight.
 * Only restore the composer after that attempt has failed or after a remount,
 * when there is no live initial submission to finish it. */
export function shouldRestoreRuntimePending(
  item: RuntimePendingTurn,
  runs: { request_id?: string }[],
  currentRequestId?: string,
  initialSubmission?: { requestId: string; status: 'sending' | 'submitted' | 'failed' },
): boolean {
  return !pendingAccepted(item, runs)
    && currentRequestId !== item.requestId
    && !(initialSubmission?.requestId === item.requestId && initialSubmission.status !== 'failed');
}

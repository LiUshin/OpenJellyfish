/** Mid-run message queue item (FIFO after current run). */
export type QueryQueueMode = 'queue';

export interface QueryQueueItem {
  id: string;
  content: string;
  mode: QueryQueueMode;
}

export function newQueueItem(content: string, mode: QueryQueueMode = 'queue'): QueryQueueItem {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    content,
    mode,
  };
}

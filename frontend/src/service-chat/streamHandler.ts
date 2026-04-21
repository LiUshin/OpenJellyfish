/**
 * Lightweight SSE → StreamBlock[] handler for service-chat.
 *
 * 与 admin 的 streamContext.tsx 共享 StreamBlock 类型，但故意写成更小的纯函数版本：
 * - 不需要 subagent / interrupt / planSteps / HITL / multi-conv 等 admin 专属状态
 * - 不依赖 React context；只暴露一个 hook
 *
 * 这样 admin 与 service 共用同一份 StreamBlock 数据结构和同一份 StreamingMessage
 * 渲染组件，但各自管理事件流到 blocks 的转换（admin 走 streamContext，service 走这里）。
 */

import { useCallback, useRef, useState } from 'react';
import type { StreamBlock } from '../pages/Chat/types';
import { openChatStream, AuthError, type ServiceChatRequest } from './serviceApi';

export interface UseServiceStreamReturn {
  blocks: StreamBlock[];
  isStreaming: boolean;
  reset: () => void;
  send: (apiKey: string, req: ServiceChatRequest) => Promise<void>;
  abort: () => void;
}

interface CallbackOpts {
  onAuthError?: () => void;
  onError?: (msg: string) => void;
  /** 流结束时调用；finalBlocks 由 hook 直接传入，避免消费者闭包到 stale state。 */
  onDone?: (finalBlocks: StreamBlock[]) => void;
}

export function useServiceStream(opts: CallbackOpts = {}): UseServiceStreamReturn {
  // 用 ref 存最新 callbacks，避免 send 因 opts 变化而每次 render 都重建
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const [blocks, setBlocks] = useState<StreamBlock[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);

  const blocksRef = useRef<StreamBlock[]>([]);
  const rafRef = useRef<number>(0);
  const pendingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const scheduleFlush = useCallback(() => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    rafRef.current = requestAnimationFrame(() => {
      pendingRef.current = false;
      setBlocks([...blocksRef.current]);
    });
  }, []);

  const flushNow = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    pendingRef.current = false;
    setBlocks([...blocksRef.current]);
  }, []);

  const reset = useCallback(() => {
    blocksRef.current = [];
    flushNow();
  }, [flushNow]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  const send = useCallback(
    async (apiKey: string, req: ServiceChatRequest) => {
      blocksRef.current = [];
      flushNow();
      setIsStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const res = await openChatStream(apiKey, req, controller.signal);
        if (!res.ok) {
          optsRef.current.onError?.(`HTTP ${res.status}`);
          blocksRef.current.push({ type: 'text', content: `❌ Error: ${res.status}` });
          flushNow();
          return;
        }

        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        // ── 局部 helper ────────────────────────────────────────────
        const lastBlock = () => blocksRef.current[blocksRef.current.length - 1];
        const closeThinking = () => {
          const last = lastBlock();
          if (last?.type === 'thinking') last.collapsed = true;
        };
        const ensureText = () => {
          const last = lastBlock();
          if (!last || last.type !== 'text') {
            blocksRef.current.push({ type: 'text', content: '' });
          }
        };
        const findOpenTool = (name?: string) => {
          for (let i = blocksRef.current.length - 1; i >= 0; i--) {
            const b = blocksRef.current[i];
            if (b.type === 'tool' && !b.done && (!name || b.name === name)) return b;
          }
          return null;
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const raw = line.slice(6).trim();
            if (!raw || raw === '[DONE]') continue;

            let evt: { type?: string; content?: string; name?: string; args_delta?: string };
            try {
              evt = JSON.parse(raw);
            } catch {
              continue;
            }

            switch (evt.type) {
              case 'token': {
                closeThinking();
                ensureText();
                const last = lastBlock() as StreamBlock & { type: 'text' };
                last.content += evt.content ?? '';
                scheduleFlush();
                break;
              }
              case 'thinking': {
                const last = lastBlock();
                if (last?.type === 'thinking' && !last.collapsed) {
                  last.content += evt.content ?? '';
                } else {
                  blocksRef.current.push({
                    type: 'thinking',
                    content: evt.content ?? '',
                    collapsed: false,
                  });
                }
                scheduleFlush();
                break;
              }
              case 'tool_call': {
                closeThinking();
                blocksRef.current.push({
                  type: 'tool',
                  name: evt.name ?? '',
                  args: '',
                  result: '',
                  done: false,
                  resultCollapsed: true,
                });
                scheduleFlush();
                break;
              }
              case 'tool_call_chunk': {
                const open = findOpenTool();
                if (open) {
                  open.args += evt.args_delta ?? '';
                  scheduleFlush();
                }
                break;
              }
              case 'tool_result': {
                // 仅标记完成 —— args/result 内容不向消费者暴露
                // （ServiceToolBadge 只渲染状态，不读 result）
                const open = findOpenTool(evt.name) || findOpenTool();
                if (open) {
                  open.done = true;
                  open.result = evt.content ?? '';
                  scheduleFlush();
                }
                break;
              }
              case 'error': {
                closeThinking();
                ensureText();
                const last = lastBlock() as StreamBlock & { type: 'text' };
                last.content += `\n\n❌ ${evt.content ?? 'unknown error'}`;
                scheduleFlush();
                break;
              }
              case 'done':
                // 后端会发 done 事件，这里不做特殊处理（loop 会自然结束）
                break;
              default:
                break;
            }
          }
        }

        flushNow();
        // 关键：把 finalBlocks 作为参数显式传出，让消费者不需要闭包到 hook 的 state；
        // 浅拷贝一份，避免 reset() 之后调用方拿到被清空的引用。
        optsRef.current.onDone?.([...blocksRef.current]);
      } catch (err) {
        if (err instanceof AuthError) {
          optsRef.current.onAuthError?.();
        } else if ((err as Error).name === 'AbortError') {
          // 用户主动取消，不视作错误
        } else {
          const msg = (err as Error).message || String(err);
          blocksRef.current.push({ type: 'text', content: `❌ 网络错误: ${msg}` });
          flushNow();
          optsRef.current.onError?.(msg);
        }
      } finally {
        abortRef.current = null;
        setIsStreaming(false);
      }
    },
    [flushNow, scheduleFlush],
  );

  return { blocks, isStreaming, reset, send, abort };
}

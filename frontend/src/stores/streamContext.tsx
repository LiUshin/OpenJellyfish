import {
  createContext, useContext, useState, useRef, useCallback,
  type ReactNode,
} from 'react';
import type { StreamBlock, SubagentBlock } from '../pages/Chat/types';
import { buildFingerprintedBlocks } from '../pages/Chat/streamFlush';
import * as api from '../services/api';

export interface PlanStep {
  content: string;
  status: string;
}

interface InterruptPayload {
  actions: unknown[];
  configs: unknown;
}

export interface StreamIdentity {
  conversationId: string;
  generation: number;
}

interface StopOutcome extends api.StopChatResult {
  identity: StreamIdentity;
  pending: boolean;
}

interface StreamContextType {
  streamingConvId: string | null;
  isStreaming: boolean;
  stopState: 'idle' | 'requesting' | 'requested' | 'failed';
  stopError: string | null;
  streamBlocks: StreamBlock[];
  interruptData: InterruptPayload | null;
  planSteps: PlanStep[];
  /**
   * 当前浏览器会话内（一次刷新内）已发生过 YOLO 自动批准的会话 id 集合。
   * 用于在输入区底部显示一个不显眼的 yolo 小 tag，无须中断或徽章。
   */
  yoloApprovedConvs: Set<string>;
  startStream: (
    convId: string,
    content: string | unknown[],
    opts: StreamOpts,
  ) => void;
  resumeStream: (
    convId: string,
    decisions: unknown[],
    opts: StreamOpts,
  ) => void;
  stopStream: () => Promise<StopOutcome | null>;
  clearFinished: (identity: StreamIdentity) => boolean;
  isCurrentStream: (identity: StreamIdentity) => boolean;
  getStreamGeneration: () => number;
  restoreInterrupt: (convId: string, data: InterruptPayload) => void;
}

interface StreamOpts {
  model?: string;
  capabilities?: string[];
  plan_mode?: boolean;
  yolo?: boolean;
  lock_mode?: 'auto' | 'manual' | 'agent';
  lock_paths?: string[];
  onDone?: (convId: string, identity: StreamIdentity, continueQueue: boolean) => void;
  onError?: (convId: string, msg: string, identity: StreamIdentity) => void;
  onInterrupt?: () => void;
  onWorkspaceLock?: (mode: string, granted: string[], conflicts?: { path: string; holder: string }[]) => void;
  onRunContinued?: (convId: string, content: string, queueId: string | undefined, identity: StreamIdentity) => void;
}

const StreamContext = createContext<StreamContextType>(null!);
export const useStream = () => useContext(StreamContext);

export function StreamProvider({ children }: { children: ReactNode }) {
  const [streamingConvId, setStreamingConvId] = useState<string | null>(null);
  const [isStreaming, setIsStreamingRaw] = useState(false);
  const streamingRef = useRef(false);
  const setIsStreaming = useCallback((value: boolean) => {
    streamingRef.current = value;
    setIsStreamingRaw(value);
  }, []);
  const [stopState, setStopState] = useState<StreamContextType['stopState']>('idle');
  const [stopError, setStopError] = useState<string | null>(null);
  const [streamBlocks, setStreamBlocks] = useState<StreamBlock[]>([]);
  const [interruptData, setInterruptDataRaw] = useState<InterruptPayload | null>(null);
  const interruptDataRef = useRef<InterruptPayload | null>(null);
  const setInterruptData = useCallback((value: InterruptPayload | null) => {
    interruptDataRef.current = value;
    setInterruptDataRaw(value);
  }, []);
  const [planSteps, setPlanSteps] = useState<PlanStep[]>([]);
  const [yoloApprovedConvs, setYoloApprovedConvs] = useState<Set<string>>(() => new Set());

  const blocksRef = useRef<StreamBlock[]>([]);
  // flush 时上一帧「发给 React」的对象数组 + 对应指纹；用于按 block 复用引用，
  // 只把内容真正变化的 block 换成新对象，让未变 block 的子组件 memo 命中。
  const emittedBlocksRef = useRef<StreamBlock[]>([]);
  const emittedFingerprintsRef = useRef<string[]>([]);
  const rafRef = useRef<number>(0);
  const pendingRef = useRef(false);
  const generationRef = useRef(0);
  const identityRef = useRef<StreamIdentity | null>(null);
  const stopIntentRef = useRef<StreamIdentity | null>(null);
  const stopRequestRef = useRef<{ identity: StreamIdentity; promise: Promise<StopOutcome | null> } | null>(null);
  const getStreamGeneration = useCallback(() => generationRef.current, []);
  const isCurrentStream = useCallback((identity: StreamIdentity) => (
    identityRef.current?.generation === identity.generation
    && identityRef.current.conversationId === identity.conversationId
  ), []);

  function bindStream(convId: string): StreamIdentity {
    cancelAnimationFrame(rafRef.current);
    pendingRef.current = false;
    const identity = { conversationId: convId, generation: ++generationRef.current };
    identityRef.current = identity;
    stopIntentRef.current = null;
    stopRequestRef.current = null;
    setStopState('idle');
    setStopError(null);
    setStreamingConvId(convId);
    return identity;
  }
  const doneRef = useRef<((convId: string, identity: StreamIdentity, continueQueue: boolean) => void) | null>(null);
  const errorRef = useRef<((convId: string, msg: string, identity: StreamIdentity) => void) | null>(null);
  const interruptRef = useRef<(() => void) | null>(null);
  const wsLockRef = useRef<((mode: string, granted: string[], conflicts?: { path: string; holder: string }[]) => void) | null>(null);
  const runContinuedRef = useRef<((convId: string, content: string, queueId: string | undefined, identity: StreamIdentity) => void) | null>(null);

  /**
   * 把 blocksRef 的当前状态提交到 React state（指纹感知，见 streamFlush.ts）。
   * 每帧实际重渲染的只有「正在变的最后一两个 block」，O(n²)→~O(1)。
   */
  function flushBlocksToReact() {
    const { next, nextFp } = buildFingerprintedBlocks(
      blocksRef.current,
      emittedBlocksRef.current,
      emittedFingerprintsRef.current,
    );
    emittedBlocksRef.current = next;
    emittedFingerprintsRef.current = nextFp;
    setStreamBlocks(next);
  }

  /** 清空 emitted 缓存（重置流状态时与 blocksRef=[] 同步调用）。 */
  function clearEmittedCache() {
    emittedBlocksRef.current = [];
    emittedFingerprintsRef.current = [];
  }

  function scheduleFlush() {
    if (pendingRef.current) return;
    pendingRef.current = true;
    rafRef.current = requestAnimationFrame(() => {
      pendingRef.current = false;
      flushBlocksToReact();
    });
  }

  function getLastBlock(): StreamBlock | undefined {
    return blocksRef.current[blocksRef.current.length - 1];
  }
  function findSubagentById(sid?: number): SubagentBlock | undefined {
    if (sid != null) {
      for (const b of blocksRef.current) {
        if (b.type === 'subagent' && b.subagentId === sid) return b;
      }
    }
    for (const b of blocksRef.current) {
      if (b.type === 'subagent' && b.status === 'running' && !b.done) return b;
    }
    for (let i = blocksRef.current.length - 1; i >= 0; i--) {
      const b = blocksRef.current[i];
      if (b.type === 'subagent' && !b.done) return b;
    }
    return undefined;
  }
  function ensureTextBlock() {
    const last = getLastBlock();
    if (!last || last.type !== 'text') {
      blocksRef.current.push({ type: 'text', content: '' });
    }
  }
  function closeThinking() {
    const last = getLastBlock();
    if (last?.type === 'thinking') last.collapsed = true;
  }

  function buildCallbacks(identity: StreamIdentity) {
    const callbacks = {
      onThinking(content: string) {
        const last = getLastBlock();
        if (last?.type === 'thinking') {
          last.content += content;
        } else {
          blocksRef.current.push({ type: 'thinking', content, collapsed: false });
        }
        scheduleFlush();
      },
      onToken(token: string) {
        closeThinking();
        ensureTextBlock();
        const last = getLastBlock() as StreamBlock & { type: 'text' };
        last.content += token;
        scheduleFlush();
      },
      onToolCall(name: string) {
        closeThinking();
        blocksRef.current.push({
          type: 'tool', name, args: '', result: '', done: false, resultCollapsed: true,
        });
        scheduleFlush();
      },
      onToolCallChunk(argsDelta: string) {
        for (let i = blocksRef.current.length - 1; i >= 0; i--) {
          const b = blocksRef.current[i];
          if (b.type === 'tool' && !b.done) { b.args += argsDelta; break; }
        }
        scheduleFlush();
      },
      onToolResult(name: string, content: string) {
        for (let i = blocksRef.current.length - 1; i >= 0; i--) {
          const b = blocksRef.current[i];
          if (b.type === 'tool' && !b.done && (b.name === name || i === blocksRef.current.length - 1)) {
            b.done = true;
            b.result = content;
            if (name === 'write_todos') {
              try {
                const parsed = JSON.parse(b.args);
                const todos = parsed?.todos;
                if (Array.isArray(todos) && todos.length > 0) {
                  setPlanSteps(todos.map((t: { content?: string; status?: string }) => ({
                    content: t.content ?? '',
                    status: t.status ?? 'pending',
                  })));
                }
              } catch { /* args not valid JSON yet, ignore */ }
            }
            break;
          }
        }
        scheduleFlush();
      },
      onSubagentCall(name: string, task: string, subagentId?: number) {
        closeThinking();
        blocksRef.current.push({
          type: 'subagent', name: name || '', task: task || '',
          status: 'preparing', content: '', tools: [],
          timeline: [],
          collapsed: false, done: false,
          subagentId,
        });
        scheduleFlush();
      },
      onSubagentCallChunk(argsDelta: string) {
        const last = getLastBlock();
        if (last?.type === 'subagent' && !last.done) {
          try {
            const partial = (last as unknown as { _argsBuf?: string })._argsBuf || '';
            const buf = partial + argsDelta;
            (last as unknown as { _argsBuf: string })._argsBuf = buf;
            try {
              const parsed = JSON.parse(buf);
              last.name = parsed.subagent_type || parsed.name || last.name;
              last.task = parsed.description || parsed.task || last.task;
            } catch {
              const nm = buf.match(/"(?:subagent_type|name)"\s*:\s*"([^"]+)"/);
              if (nm) last.name = nm[1];
              const tk = buf.match(/"(?:description|task)"\s*:\s*"((?:[^"\\]|\\.)*)"/);
              if (tk) last.task = tk[1].replace(/\\n/g, '\n').replace(/\\"/g, '"');
            }
          } catch { /* ignore */ }
          scheduleFlush();
        }
      },
      onSubagentStart(name: string, subagentId?: number) {
        const sa = findSubagentById(subagentId);
        if (sa) {
          if (name) sa.name = name;
          sa.status = 'running';
          scheduleFlush();
        }
      },
      onSubagentToken(_content: string, _agent: string, subagentId?: number) {
        const sa = findSubagentById(subagentId);
        if (sa) {
          sa.content += _content;
          const tl = sa.timeline;
          const tail = tl.length > 0 ? tl[tl.length - 1] : null;
          if (tail && tail.kind === 'text') {
            tail.content = (tail.content || '') + _content;
          } else {
            tl.push({ kind: 'text', content: _content });
          }
          scheduleFlush();
        }
      },
      onSubagentThinking(_content: string, _agent: string, subagentId?: number) {
        const sa = findSubagentById(subagentId);
        if (sa) {
          sa.content += _content;
          const tl = sa.timeline;
          const tail = tl.length > 0 ? tl[tl.length - 1] : null;
          if (tail && tail.kind === 'thinking') {
            tail.content = (tail.content || '') + _content;
          } else {
            tl.push({ kind: 'thinking', content: _content });
          }
          scheduleFlush();
        }
      },
      onSubagentToolCall(name: string, _args: string, _agent: string, subagentId?: number) {
        const sa = findSubagentById(subagentId);
        if (sa) {
          sa.tools.push({ name, done: false });
          sa.timeline.push({ kind: 'tool', toolName: name, toolDone: false });
          scheduleFlush();
        }
      },
      onSubagentToolChunk() { /* no-op */ },
      onSubagentToolResult(name: string, _content: string, _agent: string, subagentId?: number) {
        const sa = findSubagentById(subagentId);
        if (sa) {
          const tool = sa.tools.find((t) => t.name === name && !t.done);
          if (tool) tool.done = true;
          for (let i = sa.timeline.length - 1; i >= 0; i--) {
            const e = sa.timeline[i];
            if (e.kind === 'tool' && e.toolName === name && !e.toolDone) {
              e.toolDone = true;
              break;
            }
          }
          scheduleFlush();
        }
      },
      onAutoApprove(_count: number, _actions: { name: string; args: unknown }[]) {
        // YOLO 自动批准：不再向消息流插入显眼徽章，仅记录当前会话发生过自动批准，
        // 由 Chat 页面在输入区底部显示一个不显眼的小 tag（直到刷新或切换会话失效）。
        closeThinking();
        const cid = identity.conversationId;
        if (cid) {
          setYoloApprovedConvs((prev) => {
            if (prev.has(cid)) return prev;
            const next = new Set(prev);
            next.add(cid);
            return next;
          });
        }
      },
      onSubagentEnd(name: string, _result: string, subagentId?: number) {
        const sa = findSubagentById(subagentId);
        if (sa) {
          sa.done = true;
          sa.status = 'done';
          sa.collapsed = true;
          if (name) sa.name = name;
        }
        scheduleFlush();
      },
      onDone() {
        cancelAnimationFrame(rafRef.current);
        pendingRef.current = false;
        flushBlocksToReact();
        setIsStreaming(false);
        const cid = identity.conversationId;
        if (cid) doneRef.current?.(cid, identity, stopIntentRef.current?.generation !== identity.generation);
      },
      onError(msg: string) {
        cancelAnimationFrame(rafRef.current);
        pendingRef.current = false;
        blocksRef.current.push({ type: 'text', content: `❌ 错误: ${msg}` });
        flushBlocksToReact();
        setIsStreaming(false);
        const cid = identity.conversationId;
        if (cid) errorRef.current?.(cid, msg, identity);
      },
      onInterrupt(actions: unknown[], configs: unknown) {
        cancelAnimationFrame(rafRef.current);
        pendingRef.current = false;
        flushBlocksToReact();
        setIsStreaming(false);
        setInterruptData({ actions, configs });
        interruptRef.current?.();
      },
      onWorkspaceLock(mode: string, granted: string[], conflicts?: { path: string; holder: string }[]) {
        wsLockRef.current?.(mode, granted, conflicts);
      },
      onRunContinued(content: string, queueId?: string) {
        blocksRef.current = [];
        clearEmittedCache();
        scheduleFlush();
        const cid = identity.conversationId;
        if (cid) runContinuedRef.current?.(cid, content, queueId, identity);
      },
    };
    // An aborted reader can still deliver buffered events. Fence every callback,
    // including terminal/error events, before it can touch the shared stream.
    return Object.fromEntries(Object.entries(callbacks).map(([name, callback]) => [
      name, (...args: unknown[]) => {
        if (isCurrentStream(identity)) Reflect.apply(callback, undefined, args);
      },
    ])) as typeof callbacks;
  }

  const startStream = useCallback((
    convId: string,
    content: string | unknown[],
    opts: StreamOpts,
  ) => {
    const identity = bindStream(convId);
    setIsStreaming(true);
    setInterruptData(null);
    blocksRef.current = [];
    clearEmittedCache();
    setStreamBlocks([]);
    setPlanSteps([]);

    doneRef.current = opts.onDone ?? null;
    errorRef.current = opts.onError ?? null;
    interruptRef.current = opts.onInterrupt ?? null;
    wsLockRef.current = opts.onWorkspaceLock ?? null;
    runContinuedRef.current = opts.onRunContinued ?? null;

    api.streamChat(convId, content, buildCallbacks(identity), {
      model: opts.model,
      capabilities: opts.capabilities?.length ? opts.capabilities : undefined,
      plan_mode: opts.plan_mode,
      yolo: opts.yolo,
      lock_mode: opts.lock_mode,
      lock_paths: opts.lock_paths?.length ? opts.lock_paths : undefined,
    });
  }, []);

  const resumeStream = useCallback((
    convId: string,
    decisions: unknown[],
    opts: StreamOpts,
  ) => {
    const identity = bindStream(convId);
    setInterruptData(null);
    setIsStreaming(true);

    doneRef.current = opts.onDone ?? null;
    errorRef.current = opts.onError ?? null;
    interruptRef.current = opts.onInterrupt ?? null;
    wsLockRef.current = opts.onWorkspaceLock ?? null;
    runContinuedRef.current = opts.onRunContinued ?? null;

    api.resumeChat(convId, decisions, buildCallbacks(identity), {
      model: opts.model,
      capabilities: opts.capabilities?.length ? opts.capabilities : undefined,
      yolo: opts.yolo,
    });
  }, []);

  const stopStream = useCallback(async (): Promise<StopOutcome | null> => {
    const identity = identityRef.current;
    if (!identity) return null;
    if (stopRequestRef.current?.identity.generation === identity.generation) {
      return stopRequestRef.current.promise;
    }
    stopIntentRef.current = identity;
    setStopState('requesting');
    setStopError(null);
    const promise = api.stopChat(identity.conversationId).then((result) => {
      if (!isCurrentStream(identity)) return null;
      if (result.status === 'not_running' && streamingRef.current) {
        // The start request may still be preparing the agent, before the server
        // registers its cancellation flag. Keep observing it and allow retry.
        setStopState('failed');
        setStopError('后端暂未找到本轮执行，尚不能确认停止，请重试');
        return null;
      }
      setStopState('requested');
      // A live reader still owns completion even if an adapter calls its
      // acceptance "stopped". The legacy approval case has no live reader.
      const pending = streamingRef.current || result.status === 'stopping';
      if (!pending) setInterruptData(null);
      return { identity, status: result.status, pending };
    }).catch((error: unknown) => {
      if (isCurrentStream(identity)) {
        setStopState('failed');
        setStopError(error instanceof Error ? error.message : '停止请求失败');
      }
      return null;
    }).finally(() => {
      if (stopRequestRef.current?.promise === promise) stopRequestRef.current = null;
    });
    stopRequestRef.current = { identity, promise };
    return promise;
  }, [isCurrentStream]);

  const clearFinished = useCallback((identity: StreamIdentity) => {
    if (!isCurrentStream(identity)) return false;
    cancelAnimationFrame(rafRef.current);
    pendingRef.current = false;
    identityRef.current = null;
    stopIntentRef.current = null;
    setStopState('idle');
    setStopError(null);
    blocksRef.current = [];
    clearEmittedCache();
    setStreamBlocks([]);
    setStreamingConvId(null);
    setIsStreaming(false);
    setInterruptData(null);
    setPlanSteps([]);
    return true;
  }, [isCurrentStream]);

  const restoreInterrupt = useCallback((convId: string, data: InterruptPayload) => {
    // Protect live execution / approvals, but a terminal stream whose history
    // refresh failed must not permanently block another conversation's approval.
    if (identityRef.current && (streamingRef.current || interruptDataRef.current)) return;
    bindStream(convId);
    setIsStreaming(false);
    setInterruptData(data);
    blocksRef.current = [];
    clearEmittedCache();
    setStreamBlocks([]);
    setPlanSteps([]);
  }, []);

  return (
    <StreamContext.Provider value={{
      streamingConvId, isStreaming, stopState, stopError, streamBlocks, interruptData, planSteps,
      yoloApprovedConvs,
      startStream, resumeStream, stopStream, clearFinished, isCurrentStream, getStreamGeneration, restoreInterrupt,
    }}>
      {children}
    </StreamContext.Provider>
  );
}

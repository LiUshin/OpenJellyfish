import {
  createContext, useContext, useState, useRef, useCallback,
  type ReactNode,
} from 'react';
import type { StreamBlock, SubagentBlock } from '../pages/Chat/types';
import * as api from '../services/api';

export interface PlanStep {
  content: string;
  status: string;
}

interface InterruptPayload {
  actions: unknown[];
  configs: unknown;
}

interface StreamContextType {
  streamingConvId: string | null;
  isStreaming: boolean;
  streamBlocks: StreamBlock[];
  interruptData: InterruptPayload | null;
  planSteps: PlanStep[];
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
  stopStream: () => Promise<void>;
  clearFinished: () => void;
  restoreInterrupt: (convId: string, data: InterruptPayload) => void;
}

interface StreamOpts {
  model?: string;
  capabilities?: string[];
  plan_mode?: boolean;
  onDone?: (convId: string) => void;
  onError?: (convId: string, msg: string) => void;
  onInterrupt?: () => void;
}

const StreamContext = createContext<StreamContextType>(null!);
export const useStream = () => useContext(StreamContext);

export function StreamProvider({ children }: { children: ReactNode }) {
  const [streamingConvId, setStreamingConvId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamBlocks, setStreamBlocks] = useState<StreamBlock[]>([]);
  const [interruptData, setInterruptData] = useState<InterruptPayload | null>(null);
  const [planSteps, setPlanSteps] = useState<PlanStep[]>([]);

  const blocksRef = useRef<StreamBlock[]>([]);
  const rafRef = useRef<number>(0);
  const pendingRef = useRef(false);
  const convIdRef = useRef<string | null>(null);
  const doneRef = useRef<((convId: string) => void) | null>(null);
  const errorRef = useRef<((convId: string, msg: string) => void) | null>(null);
  const interruptRef = useRef<(() => void) | null>(null);

  function scheduleFlush() {
    if (pendingRef.current) return;
    pendingRef.current = true;
    rafRef.current = requestAnimationFrame(() => {
      pendingRef.current = false;
      setStreamBlocks([...blocksRef.current]);
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

  function buildCallbacks() {
    return {
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
        setStreamBlocks([...blocksRef.current]);
        setIsStreaming(false);
        const cid = convIdRef.current;
        if (cid) doneRef.current?.(cid);
      },
      onError(msg: string) {
        cancelAnimationFrame(rafRef.current);
        pendingRef.current = false;
        blocksRef.current.push({ type: 'text', content: `❌ 错误: ${msg}` });
        setStreamBlocks([...blocksRef.current]);
        setIsStreaming(false);
        const cid = convIdRef.current;
        if (cid) errorRef.current?.(cid, msg);
      },
      onInterrupt(actions: unknown[], configs: unknown) {
        cancelAnimationFrame(rafRef.current);
        pendingRef.current = false;
        setStreamBlocks([...blocksRef.current]);
        setIsStreaming(false);
        setInterruptData({ actions, configs });
        interruptRef.current?.();
      },
    };
  }

  const startStream = useCallback((
    convId: string,
    content: string | unknown[],
    opts: StreamOpts,
  ) => {
    setStreamingConvId(convId);
    convIdRef.current = convId;
    setIsStreaming(true);
    setInterruptData(null);
    blocksRef.current = [];
    setStreamBlocks([]);
    setPlanSteps([]);

    doneRef.current = opts.onDone ?? null;
    errorRef.current = opts.onError ?? null;
    interruptRef.current = opts.onInterrupt ?? null;

    api.streamChat(convId, content, buildCallbacks(), {
      model: opts.model,
      capabilities: opts.capabilities?.length ? opts.capabilities : undefined,
      plan_mode: opts.plan_mode,
    });
  }, []);

  const resumeStream = useCallback((
    convId: string,
    decisions: unknown[],
    opts: StreamOpts,
  ) => {
    setInterruptData(null);
    setIsStreaming(true);

    doneRef.current = opts.onDone ?? null;
    errorRef.current = opts.onError ?? null;
    interruptRef.current = opts.onInterrupt ?? null;

    api.resumeChat(convId, decisions, buildCallbacks(), {
      model: opts.model,
      capabilities: opts.capabilities?.length ? opts.capabilities : undefined,
    });
  }, []);

  const stopStream = useCallback(async () => {
    cancelAnimationFrame(rafRef.current);
    pendingRef.current = false;
    blocksRef.current.push({ type: 'text', content: '\n\n⚠️ 已中止' });
    setStreamBlocks([...blocksRef.current]);
    setIsStreaming(false);
    setInterruptData(null);

    const cid = convIdRef.current;
    if (cid) {
      try { await api.stopChat(cid); } catch { /* ignore */ }
    } else {
      api.abortStream();
    }
  }, []);

  const clearFinished = useCallback(() => {
    blocksRef.current = [];
    setStreamBlocks([]);
    setStreamingConvId(null);
    setInterruptData(null);
    setPlanSteps([]);
  }, []);

  const restoreInterrupt = useCallback((convId: string, data: InterruptPayload) => {
    setStreamingConvId(convId);
    setIsStreaming(false);
    setInterruptData(data);
    blocksRef.current = [];
    setStreamBlocks([]);
  }, []);

  return (
    <StreamContext.Provider value={{
      streamingConvId, isStreaming, streamBlocks, interruptData, planSteps,
      startStream, resumeStream, stopStream, clearFinished, restoreInterrupt,
    }}>
      {children}
    </StreamContext.Provider>
  );
}

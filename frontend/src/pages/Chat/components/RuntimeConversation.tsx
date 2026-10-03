import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Modal, Space, Tag, Typography } from 'antd';
import { CaretDown } from '@phosphor-icons/react';
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso';
import { useTranslation } from 'react-i18next';
import * as runtime from '../../../services/runtime';
import type { Message } from '../../../types';
import MessageBubble from './MessageBubble';
import ImageAttachment, { type ImageAttachmentHandle } from './ImageAttachment';
import ChatModelSelect from '../../../components/ChatModelSelect';
import ChatComposer from './ChatComposer';
import FileTokenInput from './FileTokenInput';
import StreamingMessage from './StreamingMessage';
import RuntimeApprovalCard from './RuntimeApprovalCard';
import QueryNavigation, { type QueryNavigationItem } from './QueryNavigation';
import { answerPreview, plainPreview } from '../utils/userQueryPreview';
import { appendRuntimeEvent } from '../utils/runtimeBlocks';
import { runtimePresentation } from '../utils/runtimePresentation';
import type { RuntimeQueuedTurn } from '../types/runtimeQueue';
import { shouldRestoreRuntimePending, type RuntimePendingTurn } from '../types/runtimePending';
import QueryQueuePanel from './QueryQueuePanel';
import ChatWelcome from './ChatWelcome';
import type { FileTokenInputHandle } from './FileTokenInput';
import styles from '../chat.module.css';
import { getYoloMode } from '../../../utils/yoloMode';

const statuses: Record<string, string> = {
  queued: '等待共享连接', starting: '正在准备会话', running: '正在生成',
  waiting_approval: '等待审批', completed: '已完成', cancelled: '已停止', failed: '执行失败',
};

type RuntimeDraft = { text: string; attachments: { name: string; dataUrl: string }[] };
type TimelineRow =
  | { kind: 'run'; key: string; time: number; order: number; run: runtime.RuntimeRun }
  | { kind: 'preview'; key: string; time: number; order: number; message: Message };
export type RuntimeInitialSubmission = {
  conversationId: string; requestId: string; text: string; model?: string; yolo: boolean;
  attachments: RuntimeDraft['attachments']; status: 'sending' | 'submitted' | 'failed';
  run?: runtime.RuntimeRun; error?: string;
};
const drafts = new Map<string, RuntimeDraft>();

function rememberDraft(conversationId: string, draft: RuntimeDraft) {
  drafts.delete(conversationId);
  if (draft.text || draft.attachments.length) drafts.set(conversationId, draft);
  while (drafts.size > 5) drafts.delete(drafts.keys().next().value!);
}

function sameAttachments(left: RuntimeDraft['attachments'], right: RuntimeDraft['attachments']): boolean {
  return left.length === right.length && left.every((file, index) =>
    file.name === right[index].name && file.dataUrl === right[index].dataUrl);
}

function clearAcceptedDraft(conversationId: string, request: Pick<RuntimePendingTurn, 'text' | 'attachments'>) {
  const draft = drafts.get(conversationId);
  if (draft?.text.trim() === request.text.trim() && sameAttachments(draft.attachments, request.attachments)) {
    drafts.delete(conversationId);
  }
}

// Runs are append-only. A GET started before a POST receipt can finish later;
// keep the already visible run (and newer streamed events) in that case.
function mergeRunSnapshots(previous: runtime.RuntimeRun[], incoming: runtime.RuntimeRun[]): runtime.RuntimeRun[] {
  const byId = new Map(incoming.map(run => [run.id, run]));
  for (const run of previous) {
    const snapshot = byId.get(run.id);
    if (!snapshot || run.seq > snapshot.seq) byId.set(run.id, run);
  }
  return [...byId.values()].sort((left, right) => left.created_at - right.created_at);
}

function InputArtifact({ item, sid }: { item: runtime.RuntimeArtifact; sid: string }) {
  const [error, setError] = useState('');
  const [imageUrl, setImageUrl] = useState('');
  useEffect(() => {
    if (!item.mime.startsWith('image/')) return;
    let disposed = false;
    let url = '';
    runtime.artifact(sid, item.id, true).then(blob => {
      if (!disposed) { url = URL.createObjectURL(blob); setImageUrl(url); }
    }).catch(() => { if (!disposed) setError('图片加载失败，可点击文件名重试'); });
    return () => { disposed = true; if (url) URL.revokeObjectURL(url); };
  }, [sid, item.id, item.mime]);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ text?: string; url?: string } | null>(null);
  useEffect(() => () => { if (preview?.url && preview.url !== imageUrl) URL.revokeObjectURL(preview.url); }, [preview, imageUrl]);
  async function open() {
    setBusy(true); setError('');
    try {
      const blob = await runtime.artifact(sid, item.id, true);
      if (/\.(md|txt|csv|json|py|js|ts|yaml|yml|log)$/i.test(item.name) && blob.size < 1024 * 1024) {
        setPreview({ text: await blob.text() });
      } else {
        const url = URL.createObjectURL(blob);
        if (item.mime.startsWith('image/')) setPreview({ url });
        else {
          const a = document.createElement('a'); a.href = url; a.download = item.name.split('/').pop() || 'artifact'; a.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
      }
    } catch (e) { setError(e instanceof Error ? e.message : '产物读取失败'); }
    finally { setBusy(false); }
  }
  return <div style={{ margin: '8px 0' }}>
    {imageUrl && <img src={imageUrl} alt={item.name} onClick={() => setPreview({ url: imageUrl })} style={{ display: 'block', maxWidth: 'min(100%, 640px)', maxHeight: 440, borderRadius: 12, cursor: 'zoom-in', marginBottom: 8 }} />}
    <Space wrap style={{ maxWidth: '100%', minWidth: 0 }}><Button size="small" loading={busy} onClick={() => void open()} style={{ height: 'auto', maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere', textAlign: 'left' }}>{item.name}</Button><Typography.Text type="secondary">{Math.ceil(item.size / 1024)} KB</Typography.Text></Space>
    {error && <Alert type="error" message={error} />}
    <Modal open={!!preview} title={item.name} footer={null} onCancel={() => setPreview(null)} width={800}>
      {preview?.url ? <img src={preview.url} alt={item.name} style={{ maxWidth: '100%' }} /> : <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: '65vh', overflow: 'auto' }}>{preview?.text}</pre>}
    </Modal>
  </div>;
}

// Keep completed runs and their expanded details stable while another turn streams.
const RuntimeResponse = memo(function RuntimeResponse({ run }: { run: runtime.RuntimeRun }) {
  const presentation = useMemo(() => runtimePresentation(
    run.blocks?.length ? run.blocks : (run.output ? [{ type: 'text', content: run.output }] : []), run.artifacts,
  ), [run.blocks, run.output, run.artifacts]);
  return <StreamingMessage blocks={presentation.blocks}
    isStreaming={!runtime.terminal(run.status)} status={run.status} startedAt={run.created_at} finishedAt={run.finished_at} />;
});

const RuntimeRunRow = memo(function RuntimeRunRow({ run, sid, busy, onDecision }: {
  run: runtime.RuntimeRun; sid: string; busy: boolean;
  onDecision: (run: runtime.RuntimeRun, decision: 'accept' | 'decline') => void;
}) {
  return <div className={styles.runtimeRunRow} data-runtime-query={run.id}>
    <MessageBubble role="user" content={run.message} attachmentContent={run.attachments?.length ? <div style={{ minWidth: 0, maxWidth: '100%' }}>{run.attachments.map(item => <InputArtifact key={item.id} item={item} sid={sid} />)}</div> : undefined} />
    {(run.output || run.blocks?.length || run.artifacts.length || !runtime.terminal(run.status)) && <RuntimeResponse run={run} />}
    <div className={styles.runMeta}>
      <Space wrap>{run.binding?.model && <Typography.Text type="secondary">{run.binding.model}</Typography.Text>}<Tag color={run.status === 'failed' ? 'red' : run.status === 'completed' ? 'green' : undefined}>{statuses[run.status] || run.status}</Tag></Space>
      {run.error && <Alert type={run.status === 'failed' ? 'error' : 'info'} message={run.error} style={{ marginTop: 8 }} />}
      {run.pending && <RuntimeApprovalCard approval={run.pending} busy={busy} onDecision={decision => onDecision(run, decision)} />}
    </div>
  </div>;
});

export default function RuntimeConversation({ sid, conversationId, history, profiles, onChanged, onRunState,
  queueItems, queueInFlight, onQueueSubmit, onQueueChange, queueError, onQueueRetry, refreshToken, onConfigure, initialSubmission,
  pending, onPendingStart, onPendingResolved, traceHidden = false }: {
  sid: string; conversationId: string; history: Message[]; profiles: runtime.RuntimeProfile[]; onChanged: () => void;
  onRunState?: (status: string | null) => void;
  queueItems: RuntimeQueuedTurn[];
  queueInFlight: boolean;
  onQueueSubmit: (item: RuntimeQueuedTurn) => void;
  onQueueChange: (items: RuntimeQueuedTurn[]) => void;
  queueError?: string;
  onQueueRetry: () => void;
  refreshToken: number;
  onConfigure: () => void;
  initialSubmission?: RuntimeInitialSubmission;
  pending?: RuntimePendingTurn;
  onPendingStart: (item: RuntimePendingTurn) => boolean;
  onPendingResolved: (requestId: string, run: runtime.RuntimeRun) => void;
  traceHidden?: boolean;
}) {
  const { t } = useTranslation();
  const [session, setSession] = useState<runtime.RuntimeSession | null>(null);
  const [runs, setRuns] = useState<runtime.RuntimeRun[]>([]);
  const [text, setText] = useState(() => drafts.get(conversationId)?.text || '');
  const [attachments, setAttachments] = useState<{ name: string; dataUrl: string }[]>(() => drafts.get(conversationId)?.attachments || []);
  const attachmentRef = useRef<ImageAttachmentHandle>(null);
  const inputRef = useRef<FileTokenInputHandle>(null);
  const [model, setModel] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submittingRef = useRef(false);
  const [submittingRequestId, setSubmittingRequestId] = useState<string | null>(null);
  const [liveTool, setLiveTool] = useState('');
  const runsRef = useRef(runs); runsRef.current = runs;
  const onChangedRef = useRef(onChanged); onChangedRef.current = onChanged;
  const onPendingResolvedRef = useRef(onPendingResolved); onPendingResolvedRef.current = onPendingResolved;
  const requestRef = useRef<{ id: string; text: string; model: string | undefined; attachments: typeof attachments; yolo: boolean } | null>(null);
  const reconciledInitialRef = useRef<string | null>(null);
  const reconciledPendingRef = useRef<string | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const [scrollParent, setScrollParent] = useState<HTMLDivElement | null>(null);
  const bindScroll = useCallback((element: HTMLDivElement | null) => { scroll.current = element; setScrollParent(element); }, []);
  const virtuoso = useRef<VirtuosoHandle>(null);
  const atBottom = useRef(true);
  const initialAutoScroll = useRef(true);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const active = runs.find(r => !runtime.terminal(r.status));
  const submitting = submittingRequestId === requestRef.current?.id
    && !runs.some(run => run.request_id === submittingRequestId) ? requestRef.current : null;
  const showInitialSubmission = !!initialSubmission && initialSubmission.status !== 'failed'
    && !runs.some(run => run.request_id === initialSubmission.requestId);
  const settledRuns = useMemo(() => runs.filter(r => runtime.terminal(r.status)), [runs]);
  const previewMessages = useMemo(() => history.filter(message => !!message.test_service_id), [history]);
  const timelineRows = useMemo<TimelineRow[]>(() => [
    ...settledRuns.map((run, index) => ({ kind: 'run' as const, key: run.id, time: run.created_at,
      order: index, run })),
    ...previewMessages.map((message, index) => {
      const parsed = Date.parse(message.timestamp || '');
      return { kind: 'preview' as const, key: `preview-${index}-${message.timestamp || ''}`,
        time: Number.isFinite(parsed) ? parsed / 1000 : Number.MAX_SAFE_INTEGER,
        order: settledRuns.length + index, message };
    }),
  ].sort((left, right) => left.time - right.time || left.order - right.order), [settledRuns, previewMessages]);
  useEffect(() => { rememberDraft(conversationId, { text, attachments }); }, [conversationId, text, attachments]);
  useEffect(() => { if (session) onRunState?.(active?.status || null); }, [session, active?.status, onRunState]);
  const [activeQuery, setActiveQuery] = useState('');
  const previewCache = useRef(new WeakMap<runtime.RuntimeRun, QueryNavigationItem>());
  const queryItems = useMemo(() => runs.map(run => {
    let item = previewCache.current.get(run);
    if (!item) {
      item = { id: run.id, question: plainPreview(run.message), answer: answerPreview(run.blocks, run.output) };
      previewCache.current.set(run, item);
    }
    return item;
  }), [runs]);
  const jumpToQuery = useCallback((id: string) => {
    const row = Array.from(scroll.current?.querySelectorAll<HTMLElement>('[data-runtime-query]') || []).find(el => el.dataset.runtimeQuery === id);
    atBottom.current = false;
    setIsAtBottom(false);
    if (row && scroll.current) {
      scroll.current.scrollTop += row.getBoundingClientRect().top - scroll.current.getBoundingClientRect().top - 12;
    } else {
      const index = timelineRows.findIndex(row => row.kind === 'run' && row.run.id === id);
      if (index >= 0) virtuoso.current?.scrollToIndex({ index, align: 'start', behavior: 'auto' });
    }
    setActiveQuery(id);
  }, [timelineRows]);
  useEffect(() => {
    const el = scroll.current;
    if (!el) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const rows = el.querySelectorAll<HTMLElement>('[data-runtime-query]');
      let id = rows[0]?.dataset.runtimeQuery || '';
      const baseline = el.getBoundingClientRect().top + 96;
      rows.forEach(row => { if (row.getBoundingClientRect().top <= baseline) id = row.dataset.runtimeQuery || ''; });
      setActiveQuery(id);
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(update); };
    el.addEventListener('scroll', onScroll, { passive: true }); onScroll();
    return () => { el.removeEventListener('scroll', onScroll); cancelAnimationFrame(raf); };
  }, [runs]);

  const refresh = useCallback(async () => {
    const value = await runtime.session(sid);
    setSession(value); setRuns(previous => mergeRunSnapshots(previous, value.runs));
    return value;
  }, [sid]);

  useEffect(() => {
    if (initialSubmission?.status === 'submitted' && initialSubmission.run) {
      setRuns(prev => prev.some(run => run.id === initialSubmission.run!.id) ? prev : [...prev, initialSubmission.run!]);
      // A failed first submission may have restored the draft before its POST
      // was reconciled. The accepted run ends that retry, even if the parent
      // has already removed its pending entry.
      if (requestRef.current?.id === initialSubmission.requestId) {
        requestRef.current = null;
        clearAcceptedDraft(conversationId, initialSubmission);
        setText(previous => previous.trim() === initialSubmission.text.trim() ? '' : previous);
        setAttachments(previous => sameAttachments(previous, initialSubmission.attachments) ? [] : previous);
        setError('');
      }
    }
    if (initialSubmission?.status === 'failed' && !runs.some(run => run.request_id === initialSubmission.requestId)) {
      setText(prev => prev || initialSubmission.text);
      setAttachments(prev => prev.length ? prev : initialSubmission.attachments);
      requestRef.current = { id: initialSubmission.requestId, text: initialSubmission.text,
        model: initialSubmission.model, attachments: initialSubmission.attachments, yolo: initialSubmission.yolo };
      setError(initialSubmission.error || '发送失败，请重试');
    }
  }, [initialSubmission?.status, initialSubmission?.requestId, initialSubmission?.run?.id]);

  useEffect(() => {
    if (initialSubmission?.status !== 'failed' || reconciledInitialRef.current === initialSubmission.requestId
      || !runs.some(run => run.request_id === initialSubmission.requestId)) return;
    reconciledInitialRef.current = initialSubmission.requestId;
    setText(prev => prev === initialSubmission.text ? '' : prev);
    setAttachments(prev => prev === initialSubmission.attachments ? [] : prev);
    if (requestRef.current?.id === initialSubmission.requestId) requestRef.current = null;
    setError('');
  }, [runs, initialSubmission]);

  useEffect(() => {
    if (!pending || !shouldRestoreRuntimePending(pending, runs, requestRef.current?.id, initialSubmission)) return;
    requestRef.current = { id: pending.requestId, text: pending.text, model: pending.model,
      attachments: pending.attachments, yolo: pending.yolo };
    setText(previous => previous || pending.text);
    setAttachments(previous => previous.length ? previous : pending.attachments);
    setModel(previous => previous || pending.model || null);
    setError('消息提交状态待确认；刷新状态后可用原请求编号重试');
  }, [pending, runs]);

  useEffect(() => {
    if (!pending || reconciledPendingRef.current === pending.requestId) return;
    const accepted = runs.find(run => run.request_id === pending.requestId);
    if (!accepted) return;
    reconciledPendingRef.current = pending.requestId;
    clearAcceptedDraft(conversationId, pending);
    setText(previous => previous.trim() === pending.text.trim() ? '' : previous);
    setAttachments(previous => sameAttachments(previous, pending.attachments) ? [] : previous);
    if (requestRef.current?.id === pending.requestId) requestRef.current = null;
    setError('');
    onPendingResolvedRef.current(pending.requestId, accepted);
  }, [conversationId, pending, runs]);

  useEffect(() => {
    if (refreshToken) void refresh().catch(e => setError(e instanceof Error ? e.message : '状态刷新失败'));
  }, [refreshToken, refresh]);

  useEffect(() => {
    let disposed = false;
    runtime.session(sid).then(value => {
      if (!disposed) { setSession(value); setRuns(previous => mergeRunSnapshots(previous, value.runs)); }
    }).catch(e => { if (!disposed) setError(e.message); });
    return () => { disposed = true; };
  }, [sid]);

  useEffect(() => {
    if (!active) return;
    const rid = active.id;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let delay = 1000;
    function consume(event: runtime.RuntimeEvent) {
      if (controller.signal.aborted) return;
      const { type, payload, seq } = event;
      setRuns(prev => prev.map(r => {
        if (r.id !== rid || seq <= r.seq) return r;
        const next = { ...r, seq, blocks: appendRuntimeEvent(r.blocks?.length ? r.blocks : (r.output ? [{ type: 'text', content: r.output }] : []), event) };
        if (type === 'text_delta') next.output += payload.text || '';
        if (statuses[type]) next.status = type;
        if (type === 'running') { next.started_at = payload.started_at; next.client_acquired_at = payload.client_acquired_at; }
        if (type === 'approval_requested') { next.pending = payload.approval || null; next.status = 'waiting_approval'; }
        if (type === 'approval_resolved') { next.pending = null; next.status = 'running'; }
        if (type === 'artifact_created' && payload.artifact) next.artifacts = [...r.artifacts, payload.artifact];
        if (runtime.terminal(type)) { next.pending = null; next.error = payload.message; }
        return next;
      }));
      if (type === 'tool' || type === 'business_tool') setLiveTool(`${payload.name || payload.kind || '工具'} · ${payload.status || ''}`);
      if (runtime.terminal(type)) { setLiveTool(''); onChangedRef.current(); }
    }
    const connect = async () => {
      try {
        const cursor = runsRef.current.find(r => r.id === rid)?.seq || 0;
        await runtime.watchRun(rid, cursor, controller.signal, consume);
        if (!controller.signal.aborted) { setError(''); await refresh(); }
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e instanceof Error ? `${e.message}。任务仍在服务器继续，正在重连。` : '正在恢复运行连接');
        try {
          const snapshot = await runtime.session(sid);
          if (controller.signal.aborted) return;
          setSession(snapshot); setRuns(previous => mergeRunSnapshots(previous, snapshot.runs));
          if (snapshot.runs.some(r => r.id === rid && runtime.terminal(r.status))) { setError(''); onChangedRef.current(); return; }
        } catch { /* keep reconnecting; never submit the turn again */ }
        if (!controller.signal.aborted) { timer = setTimeout(connect, delay); delay = Math.min(10000, delay * 2); }
      }
    };
    void connect();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [active?.id, sid, refresh]);

  useEffect(() => {
    if (scroll.current && atBottom.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [runs, previewMessages]);
  useEffect(() => {
    if (!scrollParent || (!timelineRows.length && !active && !submitting)) return;
    if (!atBottom.current && !initialAutoScroll.current) return;
    const follow = (force = false) => {
      if (timelineRows.length) virtuoso.current?.scrollToIndex({ index: 'LAST', align: 'end', behavior: 'auto' });
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (scroll.current && (force || atBottom.current || initialAutoScroll.current)) {
          scroll.current.scrollTop = scroll.current.scrollHeight;
          atBottom.current = true;
          setIsAtBottom(true);
        }
      }));
    };
    follow(initialAutoScroll.current);
    if (initialAutoScroll.current) {
      const timer = window.setTimeout(() => {
        if (!initialAutoScroll.current) return;
        follow(true);
        requestAnimationFrame(() => requestAnimationFrame(() => { initialAutoScroll.current = false; }));
      }, 350);
      return () => window.clearTimeout(timer);
    }
  }, [scrollParent, timelineRows.length, active?.id, submitting?.id]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await fn(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
    finally { setBusy(false); }
  }

  async function send(override?: string) {
    const messageText = (override ?? text).trim();
    if ((!messageText && !attachments.length) || busy || submittingRef.current || !session) return;
    if (messageText.length > 32000) { setError(t('chat.messageTooLong')); return; }
    const chosen = model || session.binding.model;
    const previous = requestRef.current ?? (pending ? {
      id: pending.requestId, text: pending.text, model: pending.model,
      attachments: pending.attachments, yolo: pending.yolo,
    } : null);
    const retryingInitial = !!previous && previous.id === initialSubmission?.requestId && !model
      && previous.text === messageText && sameAttachments(previous.attachments, attachments);
    const retryingSameRequest = !!previous && previous.text === messageText && sameAttachments(previous.attachments, attachments)
      && (previous.model === chosen || retryingInitial);
    if (previous && !retryingSameRequest) {
      setError('上一条消息提交状态未确认，请恢复原内容与模型后重试，或先刷新状态');
      return;
    }
    if ((active || queueItems.some(item => item.content.trim()) || queueInFlight) && !retryingSameRequest) {
      if (attachments.length) { setError(t('chat.queueNoImages')); return; }
      onQueueSubmit({ id: crypto.randomUUID(), content: messageText, mode: 'queue', model: chosen, yolo: getYoloMode() });
      setText(''); setError(''); inputRef.current?.clear();
      return;
    }
    if (!retryingSameRequest) {
      const next = { id: crypto.randomUUID(), text: messageText, model: chosen, attachments, yolo: getYoloMode() };
      if (!onPendingStart({ conversationId, requestId: next.id, text: next.text,
        model: next.model, attachments: next.attachments, yolo: next.yolo, kind: 'reply' })) {
        setError('无法保存待确认消息；请减少附件或释放浏览器存储空间后重试，输入内容仍在');
        return;
      }
      rememberDraft(conversationId, { text: messageText, attachments });
      requestRef.current = next;
    } else if (!requestRef.current) requestRef.current = previous;
    const request = requestRef.current;
    if (!request) return;
    submittingRef.current = true;
    // The pending request ID is already durable. Move the submitted payload out
    // of the composer before awaiting a potentially slow CLI admission POST.
    setSubmittingRequestId(request.id);
    setText('');
    setAttachments([]);
    setBusy(true); setError('');
    try {
      const run = await runtime.turn(conversationId, request.id, request.text, request.model,
        request.attachments.map(f => ({ name: f.name, data_url: f.dataUrl })), request.yolo);
      setRuns(prev => prev.some(r => r.id === run.id) ? prev : [...prev, run]);
      clearAcceptedDraft(conversationId, request);
      onPendingResolved(request.id, run);
      setText(prev => prev.trim() === request.text.trim() ? '' : prev);
      setAttachments(prev => sameAttachments(prev, request.attachments) ? [] : prev);
      if (requestRef.current?.id === request.id) requestRef.current = null;
      atBottom.current = true;
      onChangedRef.current();
    } catch (e) {
      // An accepted POST can lose its response. Reconcile by request ID before
      // exposing the draft for retry; retry always keeps the original ID.
      try {
        const alreadyVisible = runsRef.current.find(run => run.request_id === request.id);
        const snapshot = alreadyVisible ? null : await refresh();
        const accepted = alreadyVisible || snapshot?.runs.find(run => run.request_id === request.id)
          || runsRef.current.find(run => run.request_id === request.id);
        if (accepted) {
          clearAcceptedDraft(conversationId, request);
          onPendingResolved(request.id, accepted);
          setText(prev => prev.trim() === request.text.trim() ? '' : prev);
          setAttachments(prev => sameAttachments(prev, request.attachments) ? [] : prev);
          if (requestRef.current?.id === request.id) requestRef.current = null;
          setError(''); onChangedRef.current();
        } else {
          rememberDraft(conversationId, { text: request.text, attachments: request.attachments });
          setText(previous => previous || request.text);
          setAttachments(previous => previous.length ? previous : request.attachments);
          setError(e instanceof Error ? `${e.message}；提交状态未确认，重试将沿用原请求` : '提交状态未确认，重试将沿用原请求');
        }
      } catch {
        rememberDraft(conversationId, { text: request.text, attachments: request.attachments });
        setText(previous => previous || request.text);
        setAttachments(previous => previous.length ? previous : request.attachments);
        setError('无法确认消息是否已提交；重试将沿用原请求');
      }
    } finally { submittingRef.current = false; setSubmittingRequestId(null); setBusy(false); }
  }

  return <>
    {!traceHidden && !!queryItems.length && <QueryNavigation items={queryItems} activeId={activeQuery} onJump={jumpToQuery} />}
    <div className={styles.messagesViewport}>
    <div className={styles.messagesContainer} ref={bindScroll}
      onWheel={() => { initialAutoScroll.current = false; }} onTouchStart={() => { initialAutoScroll.current = false; }}
      onScroll={() => { const e = scroll.current; if (e && !initialAutoScroll.current) { atBottom.current = e.scrollHeight - e.scrollTop - e.clientHeight < 80; setIsAtBottom(atBottom.current); } }}>
      {!session && history.map((message, index) => <MessageBubble key={index} role={message.role} content={message.content} blocks={message.blocks} attachments={message.attachments} conversationId={conversationId} />)}
      {!runs.length && !previewMessages.length && !showInitialSubmission && (session || !history.length) && <ChatWelcome onSuggest={prompt => { setText(prompt); inputRef.current?.focus(); }} onConfigure={onConfigure} />}
      {scrollParent && timelineRows.length > 0 && <Virtuoso ref={virtuoso} data={timelineRows} customScrollParent={scrollParent}
        initialTopMostItemIndex={timelineRows.length - 1}
        computeItemKey={(_, row) => row.key} increaseViewportBy={{ top: 600, bottom: 600 }}
        itemContent={(_, row) => row.kind === 'run'
          ? <RuntimeRunRow run={row.run} sid={sid} busy={busy}
              onDecision={(target, decision) => void act(() => runtime.approve(target.id, target.pending!.id, decision))} />
          : <div className={styles.runtimePreviewRow}>
              <span className={styles.serviceTestMessageLabel}>{t('chat.serviceTestMessage')}</span>
              <MessageBubble role={row.message.role} content={row.message.content} blocks={row.message.blocks}
                attachments={row.message.attachments} conversationId={conversationId} />
            </div>} />}
      {active && <RuntimeRunRow run={active} sid={sid} busy={busy}
        onDecision={(target, decision) => void act(() => runtime.approve(target.id, target.pending!.id, decision))} />}
      {submitting && <div className={styles.runtimeRunRow} data-runtime-query={submitting.id}>
        <MessageBubble role="user" content={submitting.text}
          attachmentContent={submitting.attachments.length ? <div>{submitting.attachments.map((item, index) => <div key={`${item.name}-${index}`}>{item.name}</div>)}</div> : undefined} />
        <StreamingMessage blocks={[]} isStreaming status="starting" />
      </div>}
      {showInitialSubmission && <div data-runtime-query={initialSubmission.requestId}>
        <MessageBubble role="user" content={initialSubmission.text}
          attachmentContent={initialSubmission.attachments.length ? <div>{initialSubmission.attachments.map(item => <div key={item.name}>{item.name}</div>)}</div> : undefined} />
        <div className={styles.composerStatus} role="status"><span className={styles.statusDot} /> 正在提交消息…</div>
      </div>}
    </div>
      <button type="button" className={`${styles.scrollBottomBtn} ${!isAtBottom && runs.length ? styles.visible : ''}`}
        onClick={() => { atBottom.current = true; setIsAtBottom(true); if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }}>
        <CaretDown size={14} /> {t('chat.backToBottom')}
      </button>
    </div>
    <div className={styles.inputArea}>
      {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => void act(refresh)}>刷新状态</Button>} style={{ marginBottom: 8 }} />}
      {queueError && <Alert type="warning" showIcon message={queueError} action={<Button size="small" onClick={onQueueRetry}>重试发送</Button>} style={{ marginBottom: 8 }} />}
      <QueryQueuePanel items={queueItems} onChange={items => onQueueChange(items as RuntimeQueuedTurn[])}
        onRemove={id => onQueueChange(queueItems.filter(item => item.id !== id))}
        lockedIds={queueItems.filter(item => item.attempted).map(item => item.id)} />
      {queueInFlight && <div className={styles.composerStatus} role="status"><span className={styles.statusDot} />正在发送排队消息…</div>}
      {active && <div className={styles.composerStatus} role="status"><span className={styles.statusDot} />
        <span>{statuses[active.status]}{liveTool ? ` · ${liveTool}` : ''}</span>
        <span className={styles.backgroundHint}>{t('chat.backgroundRun')}</span>
      </div>}
      <ChatComposer
        attachments={<ImageAttachment ref={attachmentRef} images={attachments} onImagesChange={setAttachments} disabled={busy || !!pending || !!active || !!queueItems.length || queueInFlight || showInitialSubmission} allowFiles />}
        model={session && <ChatModelSelect value={{ ...session.binding, model: model || session.binding.model }} profiles={profiles} bound disabled={busy} onChange={choice => setModel(choice.model || null)} />}
        input={<FileTokenInput ref={inputRef} value={text} onChange={setText} onSend={() => void send()} disabled={!!pending}
          placeholder={t('chat.composePlaceholder')} onImagePaste={files => { void attachmentRef.current?.addFiles(files); }} />}
        onUpload={() => attachmentRef.current?.triggerUpload()} uploadDisabled={busy || !!pending || !!active || !!queueItems.length || queueInFlight || showInitialSubmission || attachments.length >= 5}
        hasAttachments={attachments.length > 0}
        onTranscript={transcript => void send(transcript)} voiceDisabled={busy || !!pending || !session || showInitialSubmission}
        onSend={() => void send()} sendDisabled={(!text.trim() && !attachments.length) || !session || busy || showInitialSubmission || (!!pending && !!active)} sending={busy && !active}
        onStop={active ? () => void act(() => runtime.cancel(active.id)) : undefined} stopDisabled={busy}
        hint={active ? t('chat.nextModelHint') : undefined}
      />
    </div>
  </>;
}

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Modal, Space, Tag, Typography } from 'antd';
import { useTranslation } from 'react-i18next';
import * as runtime from '../../../services/runtime';
import type { Message } from '../../../types';
import MessageBubble from './MessageBubble';
import ImageAttachment, { type ImageAttachmentHandle } from './ImageAttachment';
import ChatModelSelect from '../../../components/ChatModelSelect';
import ChatComposer from './ChatComposer';
import FileTokenInput from './FileTokenInput';
import StreamingMessage from './StreamingMessage';
import QueryNavigation, { type QueryNavigationItem } from './QueryNavigation';
import { answerPreview, plainPreview } from '../utils/userQueryPreview';
import { appendRuntimeEvent } from '../utils/runtimeBlocks';
import { runtimePresentation } from '../utils/runtimePresentation';
import styles from '../chat.module.css';
import { getYoloMode } from '../../../utils/yoloMode';

const statuses: Record<string, string> = {
  queued: '等待共享连接', starting: '正在准备会话', running: '正在生成',
  waiting_approval: '等待审批', completed: '已完成', cancelled: '已停止', failed: '执行失败',
};

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
    <Space><Button size="small" loading={busy} onClick={() => void open()}>{item.name}</Button><Typography.Text type="secondary">{Math.ceil(item.size / 1024)} KB</Typography.Text></Space>
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

export default function RuntimeConversation({ sid, conversationId, history, profiles, onChanged }: {
  sid: string; conversationId: string; history: Message[]; profiles: runtime.RuntimeProfile[]; onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [session, setSession] = useState<runtime.RuntimeSession | null>(null);
  const [runs, setRuns] = useState<runtime.RuntimeRun[]>([]);
  const [text, setText] = useState('');
  const [attachments, setAttachments] = useState<{ name: string; dataUrl: string }[]>([]);
  const attachmentRef = useRef<ImageAttachmentHandle>(null);
  const [model, setModel] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [liveTool, setLiveTool] = useState('');
  const runsRef = useRef(runs); runsRef.current = runs;
  const onChangedRef = useRef(onChanged); onChangedRef.current = onChanged;
  const requestRef = useRef<{ id: string; text: string; model: string | undefined; attachments: typeof attachments; yolo: boolean } | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const active = runs.find(r => !runtime.terminal(r.status));
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
    if (row && scroll.current) {
      atBottom.current = false;
      scroll.current.scrollTop += row.getBoundingClientRect().top - scroll.current.getBoundingClientRect().top - 12;
      setActiveQuery(id);
    }
  }, []);
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
    setSession(value); setRuns(value.runs);
    return value;
  }, [sid]);

  useEffect(() => {
    let disposed = false;
    runtime.session(sid).then(value => {
      if (!disposed) { setSession(value); setRuns(value.runs); }
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
          setSession(snapshot); setRuns(snapshot.runs); runsRef.current = snapshot.runs;
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
  }, [runs]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await fn(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
    finally { setBusy(false); }
  }

  async function send(override?: string) {
    const messageText = (override ?? text).trim();
    if ((!messageText && !attachments.length) || busy || active || !session) return;
    if (messageText.length > 32000) { setError(t('chat.messageTooLong')); return; }
    const chosen = model || session.binding.model;
    if (!requestRef.current || requestRef.current.text !== messageText || requestRef.current.model !== chosen || requestRef.current.attachments !== attachments) requestRef.current = { id: crypto.randomUUID(), text: messageText, model: chosen, attachments, yolo: getYoloMode() };
    setBusy(true); setError('');
    try {
      const run = await runtime.turn(conversationId, requestRef.current.id, messageText, requestRef.current.model, attachments.map(f => ({ name: f.name, data_url: f.dataUrl })), requestRef.current.yolo);
      setRuns(prev => prev.some(r => r.id === run.id) ? prev : [...prev, run]);
      setText(''); setAttachments([]); requestRef.current = null; atBottom.current = true;
      onChangedRef.current();
    } catch (e) {
      setError(e instanceof Error ? e.message : '发送失败');
      // A lost POST response can still have queued the run. Read first, and retry
      // only with the same request ID if the user sends the same content again.
      try { await refresh(); } catch { /* preserve the original error */ }
    } finally { setBusy(false); }
  }

  return <>
    {!!queryItems.length && <QueryNavigation items={queryItems} activeId={activeQuery} onJump={jumpToQuery} />}
    <div className={styles.runtimeConnection}><Space wrap><Tag color="blue">{session?.binding.runtime === 'cursor' ? 'Cursor' : 'Codex'}</Tag><Typography.Text type="secondary">{profiles.find(p => p.id === session?.binding.profile_id)?.name || '加载连接…'}</Typography.Text></Space></div>
    <div className={styles.messagesContainer} ref={scroll} onScroll={() => { const e = scroll.current; if (e) atBottom.current = e.scrollHeight - e.scrollTop - e.clientHeight < 80; }}>
      {!session && history.map((message, index) => <MessageBubble key={index} role={message.role} content={message.content} />)}
      {!runs.length && (session || !history.length) && <div className={styles.emptyState}><Typography.Title level={3}>开始聊天</Typography.Title><Typography.Paragraph type="secondary">可使用文档副本、个人记忆和当前 admin 的服务文档。未开启 YOLO 时，执行审批会在这里显示。</Typography.Paragraph></div>}
      {runs.map(run => <div key={run.id} data-runtime-query={run.id}>
        <MessageBubble role="user" content={run.message} />
        {!!run.attachments?.length && <div style={{ margin: '0 28px 16px 52px' }}>{run.attachments.map(item => <InputArtifact key={item.id} item={item} sid={sid} />)}</div>}
        {(run.output || run.blocks?.length || run.artifacts.length || !runtime.terminal(run.status)) && <RuntimeResponse run={run} />}
        <div className={styles.runMeta}>
          <Space wrap>{run.binding?.model && <Typography.Text type="secondary">{run.binding.model}</Typography.Text>}<Tag color={run.status === 'failed' ? 'red' : run.status === 'completed' ? 'green' : undefined}>{statuses[run.status] || run.status}</Tag>
            {run.yolo && <Tag>YOLO</Tag>}
            {run.first_token_at && <Typography.Text type="secondary">首段输出 {(run.first_token_at - run.created_at).toFixed(1)} 秒{run.started_at ? `（准备 ${(run.started_at - run.created_at).toFixed(1)} 秒 · 等待模型 ${(run.first_token_at - run.started_at).toFixed(1)} 秒）` : '（含排队）'}</Typography.Text>}
          </Space>
          {run.error && <Alert type={run.status === 'failed' ? 'error' : 'info'} message={run.error} style={{ marginTop: 8 }} />}
          {run.pending && <Alert type="warning" showIcon message="引擎请求你的审批" style={{ marginTop: 12 }} description={<div>
            {run.pending.reason && <p>{run.pending.reason}</p>}
            <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 300, overflow: 'auto' }}>{run.pending.command || JSON.stringify(run.pending.changes, null, 2)}</pre>
            <Space>{run.pending.allowed.map(decision => <Button key={decision} disabled={busy} type={decision === 'accept' ? 'primary' : 'default'} onClick={() => void act(() => runtime.approve(run.id, run.pending!.id, decision))}>{decision === 'accept' ? '允许本次' : '拒绝本次'}</Button>)}</Space>
          </div>} />}
        </div>
      </div>)}
    </div>
    <div className={styles.inputArea}>
      {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => void act(refresh)}>刷新状态</Button>} style={{ marginBottom: 8 }} />}
      {active && <div className={styles.composerStatus} role="status"><span className={styles.statusDot} />
        <span>{statuses[active.status]}{liveTool ? ` · ${liveTool}` : ''}</span>
        <span className={styles.backgroundHint}>{t('chat.backgroundRun')}</span>
      </div>}
      <ChatComposer
        attachments={<ImageAttachment ref={attachmentRef} images={attachments} onImagesChange={setAttachments} disabled={busy || !!active} allowFiles />}
        model={session && <ChatModelSelect value={{ ...session.binding, model: model || session.binding.model }} profiles={profiles} bound disabled={busy} onChange={choice => setModel(choice.model || null)} />}
        input={<FileTokenInput value={text} onChange={setText} onSend={() => void send()}
          placeholder={t('chat.composePlaceholder')} onImagePaste={files => { void attachmentRef.current?.addFiles(files); }} />}
        onUpload={() => attachmentRef.current?.triggerUpload()} uploadDisabled={busy || !!active || attachments.length >= 5}
        hasAttachments={attachments.length > 0}
        onTranscript={transcript => void send(transcript)} voiceDisabled={busy || !!active || !session}
        onSend={() => void send()} sendDisabled={!!active || (!text.trim() && !attachments.length) || !session} sending={busy && !active}
        onStop={active ? () => void act(() => runtime.cancel(active.id)) : undefined} stopDisabled={busy}
        hint={active ? t('chat.nextModelHint') : undefined}
      />
    </div>
  </>;
}

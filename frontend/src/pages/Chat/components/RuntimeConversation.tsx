import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Space, Tag, Typography } from 'antd';
import * as runtime from '../../../services/runtime';
import type { Message } from '../../../types';
import MessageBubble from './MessageBubble';
import ImageAttachment, { type ImageAttachmentHandle } from './ImageAttachment';
import ChatModelSelect from '../../../components/ChatModelSelect';
import StreamingMessage from './StreamingMessage';
import { appendRuntimeEvent } from '../utils/runtimeBlocks';
import styles from '../chat.module.css';

const statuses: Record<string, string> = {
  queued: '等待共享连接', starting: '正在准备会话', running: '正在生成',
  waiting_approval: '等待审批', completed: '已完成', cancelled: '已停止', failed: '执行失败',
};

function Artifact({ item, sid, input = false }: { item: runtime.RuntimeArtifact; sid: string; input?: boolean }) {
  const [error, setError] = useState('');
  const [imageUrl, setImageUrl] = useState('');
  useEffect(() => {
    if (!item.mime.startsWith('image/')) return;
    let disposed = false;
    let url = '';
    runtime.artifact(sid, item.id, input).then(blob => {
      if (!disposed) { url = URL.createObjectURL(blob); setImageUrl(url); }
    }).catch(() => { if (!disposed) setError('图片加载失败，可点击文件名重试'); });
    return () => { disposed = true; if (url) URL.revokeObjectURL(url); };
  }, [sid, item.id, item.mime, input]);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ text?: string; url?: string } | null>(null);
  useEffect(() => () => { if (preview?.url && preview.url !== imageUrl) URL.revokeObjectURL(preview.url); }, [preview, imageUrl]);
  async function open(download: boolean) {
    setBusy(true); setError('');
    try {
      const blob = await runtime.artifact(sid, item.id, input);
      if (!download && /\.(md|txt|csv|json|py|js|ts|yaml|yml|log)$/i.test(item.name) && blob.size < 1024 * 1024) {
        setPreview({ text: await blob.text() });
      } else {
        const url = URL.createObjectURL(blob);
        if (!download && item.mime.startsWith('image/')) setPreview({ url });
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
    <Space><Button size="small" loading={busy} onClick={() => void open(false)}>{item.name}</Button><Button size="small" disabled={busy} onClick={() => void open(true)}>下载</Button><Typography.Text type="secondary">{Math.ceil(item.size / 1024)} KB</Typography.Text></Space>
    {error && <Alert type="error" message={error} />}
    <Modal open={!!preview} title={item.name} footer={null} onCancel={() => setPreview(null)} width={800}>
      {preview?.url ? <img src={preview.url} alt={item.name} style={{ maxWidth: '100%' }} /> : <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: '65vh', overflow: 'auto' }}>{preview?.text}</pre>}
    </Modal>
  </div>;
}

export default function RuntimeConversation({ sid, conversationId, history, profiles, onChanged }: {
  sid: string; conversationId: string; history: Message[]; profiles: runtime.RuntimeProfile[]; onChanged: () => void;
}) {
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
  const requestRef = useRef<{ id: string; text: string; model: string | undefined; attachments: typeof attachments } | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const active = runs.find(r => !runtime.terminal(r.status));

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

  async function send() {
    if ((!text.trim() && !attachments.length) || busy || active || !session) return;
    const chosen = model || session.binding.model;
    if (!requestRef.current || requestRef.current.text !== text || requestRef.current.model !== chosen || requestRef.current.attachments !== attachments) requestRef.current = { id: crypto.randomUUID(), text, model: chosen, attachments };
    setBusy(true); setError('');
    try {
      const run = await runtime.turn(conversationId, requestRef.current.id, text, requestRef.current.model, attachments.map(f => ({ name: f.name, data_url: f.dataUrl })));
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
    <div style={{ padding: '8px 24px' }}><Space wrap><Tag color="blue">{session?.binding.runtime === 'cursor' ? 'Cursor' : 'Codex'}</Tag><Typography.Text type="secondary">{profiles.find(p => p.id === session?.binding.profile_id)?.name || '加载连接…'}</Typography.Text></Space></div>
    <div className={styles.messagesContainer} ref={scroll} onScroll={() => { const e = scroll.current; if (e) atBottom.current = e.scrollHeight - e.scrollTop - e.clientHeight < 80; }}>
      {!session && history.map((message, index) => <MessageBubble key={index} role={message.role} content={message.content} />)}
      {!runs.length && (session || !history.length) && <div className={styles.emptyState}><Typography.Title level={3}>开始聊天</Typography.Title><Typography.Paragraph type="secondary">可使用文档副本、个人记忆和当前 admin 的服务文档。需要执行审批时会在这里显示。</Typography.Paragraph></div>}
      {runs.map(run => <div key={run.id}>
        <MessageBubble role="user" content={run.message} />
        {!!run.attachments?.length && <div style={{ margin: '0 28px 16px 52px' }}>{run.attachments.map(item => <Artifact key={item.id} item={item} sid={sid} input />)}</div>}
        {(run.output || run.blocks?.length || !runtime.terminal(run.status)) && <StreamingMessage
          blocks={run.blocks?.length ? run.blocks : (run.output ? [{ type: 'text', content: run.output }] : [])}
          isStreaming={!runtime.terminal(run.status)} /> }
        <div style={{ margin: '8px 28px 20px 52px' }}>
          <Space wrap>{run.binding?.model && <Typography.Text type="secondary">{run.binding.model}</Typography.Text>}<Tag color={run.status === 'failed' ? 'red' : run.status === 'completed' ? 'green' : undefined}>{statuses[run.status] || run.status}</Tag>
            {run.first_token_at && <Typography.Text type="secondary">首段输出 {(run.first_token_at - run.created_at).toFixed(1)} 秒{run.started_at ? `（准备 ${(run.started_at - run.created_at).toFixed(1)} 秒 · 等待模型 ${(run.first_token_at - run.started_at).toFixed(1)} 秒）` : '（含排队）'}</Typography.Text>}
            {!runtime.terminal(run.status) && <Button size="small" danger loading={busy} onClick={() => void act(() => runtime.cancel(run.id))}>停止执行</Button>}
          </Space>
          {run.error && <Alert type={run.status === 'failed' ? 'error' : 'info'} message={run.error} style={{ marginTop: 8 }} />}
          {run.pending && <Alert type="warning" showIcon message="引擎请求你的审批" style={{ marginTop: 12 }} description={<div>
            {run.pending.reason && <p>{run.pending.reason}</p>}
            <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 300, overflow: 'auto' }}>{run.pending.command || JSON.stringify(run.pending.changes, null, 2)}</pre>
            <Space>{run.pending.allowed.map(decision => <Button key={decision} disabled={busy} type={decision === 'accept' ? 'primary' : 'default'} onClick={() => void act(() => runtime.approve(run.id, run.pending!.id, decision))}>{decision === 'accept' ? '允许本次' : '拒绝本次'}</Button>)}</Space>
          </div>} />}
          {run.artifacts.map(item => <Artifact key={item.id} item={item} sid={sid} />)}
        </div>
      </div>)}
    </div>
    <div className={styles.inputArea}>
      {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => void act(refresh)}>刷新状态</Button>} style={{ marginBottom: 8 }} />}
      {active && <Typography.Paragraph type="secondary">{statuses[active.status]}{liveTool ? ` · ${liveTool}` : ''}。离开页面后任务继续运行。</Typography.Paragraph>}
      <ImageAttachment ref={attachmentRef} images={attachments} onImagesChange={setAttachments} disabled={busy || !!active} allowFiles />
      <Button size="small" disabled={busy || !!active || attachments.length >= 5} onClick={() => attachmentRef.current?.triggerUpload()} style={{ marginBottom: 8 }}>添加图片或文件</Button>
      <Input.TextArea aria-label="发送消息" value={text} onChange={e => setText(e.target.value)} autoSize={{ minRows: 2, maxRows: 8 }} maxLength={32000} placeholder="输入消息；Enter 发送，Shift+Enter 换行" onPressEnter={e => { if (!e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} />
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', paddingTop: 8 }}>
        {session && <Space wrap><ChatModelSelect value={{ ...session.binding, model: model || session.binding.model }} profiles={profiles} bound disabled={busy} onChange={choice => setModel(choice.model || null)} />
          {active && <Typography.Text type="secondary">切换模型将在下一条消息生效</Typography.Text>}</Space>}
        <Button type="primary" disabled={!!active || (!text.trim() && !attachments.length) || !session} loading={busy} onClick={() => void send()}>发送</Button></div>
    </div>
  </>;
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Input, Select, Space, Tag, Typography } from 'antd';
import { useSearchParams } from 'react-router-dom';
import { artifactBlob, pilotRequest, readEvents } from '../services/runtimePilot';
import type { Approval, Artifact, PilotEvent, Run, Session } from '../services/runtimePilot';

const { Title, Paragraph, Text } = Typography;
const terminal = (status: string) => ['completed', 'failed', 'cancelled'].includes(status);

function ArtifactPreview({ sid, artifact }: { sid: string; artifact: Artifact }) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';
    artifactBlob(sid, artifact.id).then(blob => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(e => { if (!cancelled) setError(String(e.message)); });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [sid, artifact.id]);
  return <Card size="small" style={{ maxWidth: 460 }}>
    {error && <Alert type="error" message={error} />}
    {url && artifact.mime.startsWith('image/') && <img src={url} alt={artifact.name} style={{ width: '100%', maxHeight: 320, objectFit: 'contain' }} />}
    {url ? <a href={url} download={artifact.name.split('/').pop()}>{artifact.name}</a> : <Text>{artifact.name}</Text>}
    {artifact.native_image && <Tag color="purple" style={{ marginLeft: 8 }}>原生生图</Tag>}
  </Card>;
}

export default function RuntimePilot() {
  const [params, setParams] = useSearchParams();
  const sid = params.get('session') || '';
  const [enabled, setEnabled] = useState(false);
  const [error, setError] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [events, setEvents] = useState<Record<string, PilotEvent[]>>({});
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [model, setModel] = useState<string>();
  const [paths, setPaths] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState('未检测');
  const [reload, setReload] = useState(0);
  const requestRef = useRef<{ sid: string; message: string; id: string } | null>(null);
  const active = runs.find(r => !terminal(r.status));

  const refreshList = useCallback(async () => {
    const data = await pilotRequest<Session[]>('GET', '/sessions');
    setSessions(data);
  }, []);
  useEffect(() => {
    pilotRequest('GET', '/status').then(() => { setEnabled(true); return refreshList(); })
      .catch(e => setError(e.message));
  }, [refreshList]);

  useEffect(() => {
    const controller = new AbortController();
    setEvents({}); setRuns([]); setSession(null);
    if (!sid || !enabled) return () => controller.abort();
    (async () => {
      const s = await pilotRequest<Omit<Session, 'runs'> & { runs: Run[] }>('GET', `/sessions/${sid}`);
      if (controller.signal.aborted) return;
      setSession({ ...s, runs: s.runs.map(r => r.id) });
      setRuns(s.runs);
      // Historical runs end immediately; the latest run stays attached to durable SSE.
      for (const run of s.runs) {
        await readEvents(sid, run.id, 0, event => {
          if (controller.signal.aborted) return;
          setEvents(old => ({ ...old, [run.id]: [...(old[run.id] || []), event] }));
          if (event.type === 'approval_requested') setRuns(old => old.map(r => r.id === run.id ? { ...r, pending: event.approval || null } : r));
          if (event.type === 'approval_resolved' || terminal(event.type)) setRuns(old => old.map(r => r.id === run.id ? { ...r, pending: null, ...(terminal(event.type) ? { status: event.type } : {}) } : r));
          if (event.artifact) setSession(old => old && ({ ...old, artifacts: [...old.artifacts.filter(a => a.id !== event.artifact!.id), event.artifact!] }));
        }, controller.signal);
      }
    })().catch(e => { if (!controller.signal.aborted) setError(e.message || '连接中断，请点击重新连接'); });
    return () => controller.abort();
  }, [sid, enabled, reload]);

  async function action(fn: () => Promise<void>) {
    setBusy(true); setError('');
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function respond(run: Run, approval: Approval, decision: string) {
    await action(async () => {
      await pilotRequest('POST', `/sessions/${sid}/runs/${run.id}/approve`, { approval_id: approval.id, decision });
      setRuns(old => old.map(r => r.id === run.id ? { ...r, pending: null } : r));
    });
  }

  return <div style={{ padding: '32px clamp(16px, 4vw, 56px)', maxWidth: 1100, margin: '0 auto', width: '100%', overflowY: 'auto', height: '100%' }}>
    <Tag color="purple">Jellyfish · Runtime Pilot</Tag>
    <Title level={2}>Codex 试验台</Title>
    <Paragraph type="secondary">在独立工作区中使用所选文档的副本，成果归档到 Jellyfish。会话固定使用 Codex，可刷新页面后继续。</Paragraph>
    <Alert type="info" showIcon message="首版仅供可信的自托管管理员使用" description="已接入文档副本、原生执行和产物归档。定时任务、消息发送与自定义子代理尚未接入。原生生图不可用时会报错，不会自动切换到付费 API。" style={{ marginBottom: 20 }} />
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
    <Space wrap style={{ marginBottom: 20 }}>
      <Button disabled={!enabled || busy || !!active} onClick={() => action(async () => {
        const probe = await pilotRequest<{ authenticated: boolean; models: { id: string; name: string }[] }>('POST', '/probe');
        setModels(probe.models);
        setConnection(probe.authenticated ? '已连接并登录；生图待实测' : '已连接，需要在专用 Codex 身份目录登录');
      })}>检测 Codex 连接</Button>
      <Text type="secondary">{connection}</Text>
    </Space>
    <Card title="会话" size="small" style={{ marginBottom: 20 }}>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Select aria-label="选择试验会话" placeholder="选择已有会话" value={sid || undefined} style={{ width: '100%' }}
          options={sessions.map(s => ({ value: s.id, label: `${new Date(s.created_at * 1000).toLocaleString()} · ${s.model || 'Codex 默认模型'} · ${s.id.slice(0, 8)}` }))}
          onChange={value => { setError(''); setParams({ session: value }); }} />
        <Select aria-label="新会话模型" allowClear placeholder="新会话模型：使用 Codex 默认配置" value={model} style={{ width: '100%' }}
          options={models.map(m => ({ value: m.id, label: m.name }))} onChange={setModel} />
        <Input.TextArea aria-label="导入文档路径" value={paths} onChange={e => setPaths(e.target.value)} rows={2}
          placeholder={'导入 Jellyfish 文档（可选，每行一个路径）\n/docs/brief.md'} />
        <Button disabled={!enabled || busy || !!active} onClick={() => action(async () => {
          const created = await pilotRequest<Session>('POST', '/sessions', { model, context_paths: paths.split('\n').map(p => p.trim()).filter(Boolean) });
          await refreshList(); setParams({ session: created.id });
        })}>新建 Codex 会话</Button>
      </Space>
    </Card>
    {session && <>
      {session.imports.length > 0 && <Paragraph type="secondary">已导入：{session.imports.join('、')}</Paragraph>}
      {runs.map(run => <Card key={run.id} size="small" style={{ marginBottom: 16 }}>
        <Tag color={run.status === 'failed' ? 'red' : 'purple'}>{run.status}</Tag>
        <Paragraph style={{ whiteSpace: 'pre-wrap', marginTop: 12 }}><strong>你：</strong>{run.message}</Paragraph>
        <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{(events[run.id] || []).filter(e => e.type === 'text_delta').map(e => e.text).join('')}</Paragraph>
        <details><summary>执行记录</summary>{(events[run.id] || []).filter(e => !['text_delta', 'user_message'].includes(e.type)).map(e =>
          <div key={e.seq} style={{ overflowWrap: 'anywhere', margin: '6px 0' }}><Text type="secondary">{e.type}</Text> {e.message || e.command || e.kind || ''}</div>)}</details>
        {run.pending && <Alert style={{ marginTop: 12 }} type="warning" message="Codex 请求操作批准" description={<>
          <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{run.pending.command || run.pending.reason || run.pending.kind}</pre>
          {run.pending.changes != null && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(run.pending.changes, null, 2)}</pre>}
          <Space>{run.pending.allowed.map(d => <Button key={d} disabled={busy} type={d === 'accept' ? 'primary' : 'default'} onClick={() => respond(run, run.pending!, d)}>{d === 'accept' ? '允许本次' : '拒绝'}</Button>)}</Space>
        </>} />}
      </Card>)}
      <Space wrap style={{ marginBottom: 20 }}>{session.artifacts.map(a => <ArtifactPreview key={a.id} sid={sid} artifact={a} />)}</Space>
      <Input.TextArea aria-label="发送给 Codex 的消息" rows={4} value={message} onChange={e => setMessage(e.target.value)} placeholder="描述任务，例如：阅读 docs/brief.md，写一份方案，并生成配图。" />
      <Space style={{ marginTop: 12, marginBottom: 24 }} wrap>
        <Button type="primary" disabled={busy || !!active || !message.trim()} onClick={() => action(async () => {
          const text = message.trim();
          if (!requestRef.current || requestRef.current.sid !== sid || requestRef.current.message !== text) requestRef.current = { sid, message: text, id: crypto.randomUUID().replace(/-/g, '') };
          await pilotRequest('POST', `/sessions/${sid}/turns`, { request_id: requestRef.current.id, message: text });
          requestRef.current = null; setMessage(''); setReload(n => n + 1);
        })}>发送</Button>
        <Button disabled={!active || busy} onClick={() => action(async () => {
          await pilotRequest('POST', `/sessions/${sid}/runs/${active!.id}/cancel`); setReload(n => n + 1);
        })}>停止执行</Button>
        <Button disabled={busy} onClick={() => { setError(''); setReload(n => n + 1); }}>重新连接 / 刷新记录</Button>
      </Space>
    </>}
  </div>;
}

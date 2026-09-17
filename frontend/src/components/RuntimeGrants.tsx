import { useEffect, useState } from 'react';
import { Alert, Button, Popconfirm, Select, Space, Tag, Typography } from 'antd';
import * as runtime from '../services/runtime';
import type { HostClient } from '../services/superadmin';

export default function RuntimeGrants({ profile, api }: { profile: runtime.RuntimeProfile; api: HostClient }) {
  const [admins, setAdmins] = useState<{ id: string; username: string }[]>([]);
  const [grants, setGrants] = useState<runtime.RuntimeGrant[]>([]);
  const [actor, setActor] = useState<string>();
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    Promise.all([api.admins(), api.grants(profile.id)]).then(([a, g]) => {
      if (!disposed) { setAdmins(a); setGrants(g); }
    }).catch(e => { if (!disposed) setError(e.message); });
    return () => { disposed = true; };
  }, [profile.id, profile.auth_generation, api]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await fn(); setGrants(await api.grants(profile.id)); }
    catch (e) { setError(e instanceof Error ? e.message : '授权修改失败'); }
    finally { setBusy(false); }
  }
  return <div style={{ marginTop: 16 }}>
    <Typography.Text strong>共享给可信 admin</Typography.Text>
    <Typography.Paragraph type="secondary" style={{ margin: '6px 0' }}>共享同一账号额度；每位 admin 使用各自的聊天和文件。授权不提供操作系统级隔离。</Typography.Paragraph>
    {error && <Alert type="error" message={error} style={{ marginBottom: 10 }} />}
    {grants.map(g => <div key={g.id} style={{ paddingBottom: 8 }}><Space wrap>
      <span>{admins.find(a => a.id === g.actor_id)?.username || g.actor_id}</span>
      <Tag color={g.enabled && g.auth_generation === profile.auth_generation ? 'green' : undefined}>{!g.enabled ? '已撤销' : g.auth_generation !== profile.auth_generation ? '账号已变更，需重新授权' : '已授权'}</Tag>
      <Typography.Text type="secondary">{g.models.join('、')}</Typography.Text>
      <Button size="small" disabled={busy} onClick={() => { setActor(g.actor_id); setModels(g.models.filter(m => profile.models.some(p => p.id === m))); }}>编辑 / 重新授权</Button>
      {g.enabled && <Popconfirm title="撤销后将停止此 admin 的相关任务。" onConfirm={() => act(() => api.revoke(profile.id, g.id))}><Button size="small" danger disabled={busy}>撤销</Button></Popconfirm>}
    </Space></div>)}
    <Space wrap>
      <Select aria-label="选择可信 admin" placeholder="选择 admin" value={actor} onChange={setActor} style={{ minWidth: 160 }} options={admins.map(a => ({ value: a.id, label: a.username }))} />
      <Select aria-label="授权模型" mode="multiple" placeholder="允许使用的模型" value={models} onChange={setModels} style={{ minWidth: 260, maxWidth: '100%' }} options={profile.models.map(m => ({ value: m.id, label: m.name }))} />
      <Button disabled={busy || !actor || !models.length || profile.status !== 'ready'} onClick={() => actor && void act(() => api.grant(profile.id, actor, models))}>保存授权</Button>
    </Space>
  </div>;
}

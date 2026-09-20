import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Input, Popconfirm, Select, Space, Tag, Typography } from 'antd';
import * as runtime from '../services/runtime';
import RuntimeGrants from './RuntimeGrants';
import RuntimeDefault from './RuntimeDefault';
import type { HostClient } from '../services/superadmin';

const statusNames: Record<string, string> = {
  ready: '已连接', connecting: '等待登录', disconnected: '未连接',
  disconnecting: '正在断开', error: '连接异常',
  authorization_required: '需要超管重新授权',
};

function showLoginPopup(popup: Window | null, title: string, message: string) {
  if (!popup || popup.closed) return;
  try {
    const doc = popup.document;
    doc.title = title;
    doc.documentElement.lang = 'zh-CN';
    doc.body.style.cssText = 'max-width:560px;margin:15vh auto;padding:24px;font:16px/1.7 system-ui,sans-serif;color:#24292f;background:#f6f8fa';
    const heading = doc.createElement('h1');
    heading.style.fontSize = '24px';
    heading.textContent = title;
    const content = doc.createElement('p');
    content.textContent = message;
    doc.body.replaceChildren(heading, content);
  } catch { /* The parent console still displays failures if the popup is unavailable. */ }
}

export default function RuntimeSettingsCard({ api = runtime, host = false }: { api?: HostClient; host?: boolean }) {
  const [caps, setCaps] = useState<runtime.RuntimeCapabilities | null>(null);
  const [profiles, setProfiles] = useState<runtime.RuntimeProfile[]>([]);
  const [attempt, setAttempt] = useState<runtime.LoginAttempt | null>(null);
  const [engine, setEngine] = useState<'codex' | 'cursor'>('codex');
  const [name, setName] = useState('团队 Codex');
  const [mode, setMode] = useState<'chatgpt' | 'chatgptDeviceCode'>('chatgptDeviceCode');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    const capability = await api.capabilities();
    setCaps(capability);
    if (capability.available) {
      const items = await api.profiles();
      setProfiles(items);
      const pending = items.find(p => p.can_manage && p.login_id);
      if (pending?.login_id) setAttempt(await api.loginStatus(pending.id, pending.login_id));
    }
  }, [api]);

  useEffect(() => {
    let disposed = false;
    // Initial capability fetch is cheap; do not request disabled endpoints.
    api.capabilities().then(async c => {
      if (disposed) return;
      setCaps(c);
      if (!c.available) return;
      const items = await api.profiles();
      if (disposed) return;
      setProfiles(items);
      const pending = items.find(p => p.can_manage && p.login_id);
      if (pending?.login_id) {
        const a = await api.loginStatus(pending.id, pending.login_id);
        if (!disposed) setAttempt(a);
      }
    }).catch(e => { if (!disposed) setError(String(e.message || e)); });
    return () => { disposed = true; };
  }, [api]);

  useEffect(() => {
    if (!attempt || attempt.status !== 'pending') return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api.loginStatus(attempt.profile_id, attempt.id);
        if (disposed) return;
        setAttempt(next);
        if (next.status !== 'pending') {
          await refresh();
          if (next.status !== 'completed') setError(`登录${({ cancelled: '已取消', expired: '已过期', failed: '失败，请重试' } as Record<string, string>)[next.status] || next.status}`);
          return;
        }
      } catch (e) {
        if (!disposed) setError(e instanceof Error ? e.message : '登录状态读取失败');
      }
      if (!disposed) timer = setTimeout(poll, 1500);
    };
    timer = setTimeout(poll, 1000);
    return () => { disposed = true; clearTimeout(timer); };
  }, [attempt?.id, attempt?.status, attempt?.profile_id, refresh, api]);

  useEffect(() => {
    if (!caps?.available) return;
    let disposed = false;
    const timer = setInterval(() => {
      api.profiles().then(items => { if (!disposed) setProfiles(items); }).catch(() => {});
    }, 3000);
    return () => { disposed = true; clearInterval(timer); };
  }, [caps?.available, api]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
    finally { setBusy(false); }
  }

  function startLogin(profile: runtime.RuntimeProfile) {
    // Open synchronously from the user's click; browsers block window.open
    // after an awaited request. The inline link remains available as a fallback.
    let popup: Window | null = null;
    try {
      popup = window.open('about:blank', '_blank');
      if (popup) popup.opener = null;
      showLoginPopup(popup, '正在准备账号授权…', '正在向服务器请求官方登录地址，准备好后会自动跳转。');
    } catch { popup = null; } // Embedded browsers may disable popups entirely.
    void act(async () => {
      try {
        const next = await api.login(profile.id, profile.runtime === 'cursor' ? 'cursorBrowser' : mode);
        setAttempt(next);
        if (next.challenge?.url && popup && !popup.closed) popup.location.replace(next.challenge.url);
        else popup?.close();
      } catch (e) {
        const message = e instanceof Error ? e.message : '登录启动失败';
        showLoginPopup(popup, '暂时无法开始登录', `${message} 请关闭此页，回到超管控制台处理后重试。`);
        throw e;
      }
    });
  }

  return <section className="runtime-settings-card" aria-label="Agent 引擎连接" style={{ background: 'var(--jf-bg-raised)', border: '1px solid var(--jf-border)', borderRadius: 'var(--jf-radius-lg)', padding: 20, marginBottom: 16 }}>
    <Typography.Title level={5} style={{ marginTop: 0 }}>Agent 引擎连接</Typography.Title>
    <Typography.Paragraph type="secondary">{host ? '主机主人连接自己的 Codex / Cursor 账号，并按模型授权给可信 admin。' : 'Codex / Cursor 由超管连接并授权，在这里选择默认引擎。DeepAgents 使用下方的模型配置。'}</Typography.Paragraph>
    {error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} style={{ marginBottom: 12 }} />}
    {caps && !caps.available && <Alert type="info" message={caps.reason || '外部引擎尚未启用'} />}
    {caps?.available && <>
      <Space wrap style={{ marginBottom: 12 }}><Tag>标准模式 · 本机执行</Tag><Tag>连接级预热</Tag><Button size="small" loading={busy} onClick={() => void act(refresh)}>刷新</Button></Space>
      {!profiles.length && <Typography.Paragraph type="secondary">{caps.can_manage_connections ? '创建连接后登录你的 Codex 或 Cursor 账号。' : '超管授权连接后会显示在这里。'}</Typography.Paragraph>}
      {profiles.map(p => <div key={p.id} style={{ borderTop: '1px solid var(--jf-border)', padding: '14px 0' }}>
        <Space wrap><strong>{p.name}</strong><Tag>{p.runtime === 'cursor' ? 'Cursor' : 'Codex'}</Tag><Tag color={p.status === 'ready' ? 'green' : undefined}>{statusNames[p.status] || p.status}</Tag><Tag>{host ? '主机连接' : '团队连接'}</Tag></Space>
        {p.status === 'ready' && <Space wrap style={{ marginTop: 8 }}>
          <Tag color={p.worker?.status === 'ready' ? 'green' : undefined}>{({ warming: '预热中', ready: '可服务', busy: '执行中', sleeping: '等待可用客户端', error: '预热失败，将重试' } as Record<string, string>)[p.worker?.status || ''] || '按需启动'}</Tag>
          {p.capabilities?.web_search && <Tag>网页搜索</Tag>}{p.capabilities?.image_input && <Tag>图片输入</Tag>}
          {p.capabilities?.file_input && <Tag>文件输入</Tag>}{p.capabilities?.image_generation && <Tag>原生生图</Tag>}
        </Space>}
        {p.account && <Typography.Paragraph type="secondary" style={{ margin: '8px 0' }}>{p.account.email} · {p.account.planType}</Typography.Paragraph>}
        {p.recovery_required && <Alert type="warning" message="上次执行异常退出，请由服务器主人检查旧进程后恢复。" />}
        {p.can_manage && <Space wrap style={{ marginTop: 10 }}>
          {p.runtime === 'codex' && <Select aria-label="Codex 登录方式" value={mode} onChange={setMode} options={[{ value: 'chatgptDeviceCode', label: '设备码登录（服务器）' }, { value: 'chatgpt', label: '浏览器登录（本机）' }]} />}
          <Button disabled={busy || attempt?.status === 'pending' || p.recovery_required} onClick={() => startLogin(p)}>{p.status === 'ready' ? '重新登录' : `登录 ${p.runtime === 'cursor' ? 'Cursor' : 'Codex'}`}</Button>
          {p.status === 'ready' && <Button disabled={busy} onClick={() => void act(() => api.probe(p.id))}>刷新模型</Button>}
          {p.status !== 'disconnected' && <Popconfirm title="断开后将停止此连接的所有任务。" onConfirm={() => act(() => api.disconnect(p.id))}><Button danger disabled={busy}>断开</Button></Popconfirm>}
        </Space>}
      {attempt?.profile_id === p.id && attempt.status === 'pending' && attempt.challenge && <Alert type="info" showIcon message={`完成 ${p.runtime === 'cursor' ? 'Cursor' : 'Codex'} 授权`} description={<Space direction="vertical">
        <Button type="primary" href={attempt.challenge.url} target="_blank" rel="noopener noreferrer">打开官方登录页面</Button>
        <Typography.Text copyable={{ text: attempt.challenge.url }}>复制登录链接</Typography.Text>
        {attempt.challenge.user_code && <Typography.Text copyable>设备码：{attempt.challenge.user_code}</Typography.Text>}
        <Typography.Text type="secondary">登录有效期至 {new Date(attempt.expires_at * 1000).toLocaleTimeString()}。{attempt.challenge.mode === 'chatgpt' ? '本机浏览器登录需要在服务器所在电脑完成回调。' : '请在官方页面登录并授权，完成后这里会自动更新。'}</Typography.Text>
        <Button size="small" disabled={busy} onClick={() => void act(() => api.cancelLogin(attempt.profile_id, attempt.id))}>取消登录</Button>
      </Space>} style={{ margin: '12px 0' }} />}
        {p.models.length > 0 && <Typography.Paragraph type="secondary" style={{ margin: '10px 0 0' }}>可用模型：{p.models.map(m => m.name).join('、')}</Typography.Paragraph>}
        {p.can_manage && <RuntimeGrants profile={p} api={api} />}
      </div>)}
      {caps.can_manage_connections && <Space.Compact style={{ maxWidth: 540, width: '100%' }}><Select aria-label="连接引擎" value={engine} style={{ minWidth: 120 }} onChange={v => { setEngine(v); setName(`团队 ${v === 'cursor' ? 'Cursor' : 'Codex'}`); }} options={[{ value: 'codex', label: 'Codex' }, { value: 'cursor', label: 'Cursor' }]} /><Input aria-label="连接名称" maxLength={80} value={name} onChange={e => setName(e.target.value)} /><Button disabled={busy || !name.trim()} onClick={() => void act(() => api.createProfile(name.trim(), engine))}>创建连接</Button></Space.Compact>}
      {!host && <RuntimeDefault profiles={profiles} />}
    </>}
  </section>;
}

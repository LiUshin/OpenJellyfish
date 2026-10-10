import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Popconfirm, Select, Spin, Tag, Typography } from 'antd';
import { ArrowsClockwise, Plug, Plus } from '@phosphor-icons/react';
import * as runtime from '../services/runtime';
import RuntimeGrants from './RuntimeGrants';
import RuntimeDefault from './RuntimeDefault';
import type { HostClient } from '../services/superadmin';
import { useRuntimeCopy } from './runtimeManagementCopy';
import styles from './runtimeManagement.module.css';

function showLoginPopup(popup: Window | null, title: string, message: string, english: boolean) {
  if (!popup || popup.closed) return;
  try {
    const doc = popup.document;
    doc.title = title;
    doc.documentElement.lang = english ? 'en' : 'zh-CN';
    doc.body.style.cssText = 'max-width:560px;margin:15vh auto;padding:24px;font:16px/1.7 system-ui,sans-serif;color:#24292f;background:#f6f8fa';
    const heading = doc.createElement('h1');
    heading.style.fontSize = '24px';
    heading.textContent = title;
    const content = doc.createElement('p');
    content.textContent = message;
    doc.body.replaceChildren(heading, content);
  } catch { /* The console also shows failures if the popup is unavailable. */ }
}

export default function RuntimeSettingsCard({ api = runtime, host = false }: { api?: HostClient; host?: boolean }) {
  const { text, english } = useRuntimeCopy();
  const [caps, setCaps] = useState<runtime.RuntimeCapabilities | null>(null);
  const [profiles, setProfiles] = useState<runtime.RuntimeProfile[]>([]);
  const [profilesLoaded, setProfilesLoaded] = useState(false);
  const [grantRevision, setGrantRevision] = useState(0);
  const [attempt, setAttempt] = useState<runtime.LoginAttempt | null>(null);
  const [engine, setEngine] = useState<'codex' | 'cursor'>('codex');
  const [name, setName] = useState('');
  const [mode, setMode] = useState<'chatgpt' | 'chatgptDeviceCode'>('chatgptDeviceCode');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const mounted = useRef(false);
  const readVersion = useRef(0);
  const actionPending = useRef(false);

  const refresh = useCallback(async () => {
    const version = ++readVersion.current;
    const capability = await api.capabilities();
    if (!mounted.current || version !== readVersion.current) return;
    setCaps(capability);
    if (!capability.available) {
      setProfiles([]);
      setProfilesLoaded(false);
      setAttempt(null);
      return;
    }
    const items = await api.profiles();
    if (!mounted.current || version !== readVersion.current) return;
    setProfiles(items);
    setProfilesLoaded(true);
    setGrantRevision(value => value + 1);
    const pending = items.find(p => p.can_manage && p.login_id);
    const nextAttempt = pending?.login_id ? await api.loginStatus(pending.id, pending.login_id) : null;
    if (mounted.current && version === readVersion.current) setAttempt(nextAttempt);
  }, [api]);

  useEffect(() => {
    mounted.current = true;
    setLoading(true);
    setProfilesLoaded(false);
    void refresh().catch(e => {
      if (mounted.current) setError(e instanceof Error ? e.message : String(e));
    }).finally(() => { if (mounted.current) setLoading(false); });
    return () => { mounted.current = false; readVersion.current++; };
  }, [refresh]);

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
          if (mounted.current && next.status !== 'completed') {
            setError(text('登录未完成，请重新授权。', 'Sign-in did not complete. Please authorize again.'));
          }
          return;
        }
      } catch (e) {
        if (!disposed) setError(e instanceof Error ? e.message : text('登录状态读取失败', 'Could not read sign-in status'));
      }
      if (!disposed) timer = setTimeout(poll, 1500);
    };
    timer = setTimeout(poll, 1000);
    return () => { disposed = true; clearTimeout(timer); };
  }, [attempt?.id, attempt?.status, attempt?.profile_id, refresh, api, text]);

  useEffect(() => {
    if (!caps?.available || loading || busy) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const version = ++readVersion.current;
      try {
        const items = await api.profiles();
        if (!disposed && version === readVersion.current) {
          setProfiles(items);
          setProfilesLoaded(true);
        }
      } catch { /* A manual refresh exposes errors without disrupting the current view. */ }
      if (!disposed) timer = setTimeout(poll, 3000);
    };
    timer = setTimeout(poll, 3000);
    return () => { disposed = true; clearTimeout(timer); };
  }, [caps?.available, api, busy, loading]);

  async function act(action: () => Promise<unknown>) {
    if (actionPending.current) return;
    actionPending.current = true;
    readVersion.current++;
    setBusy(true);
    setError('');
    try { await action(); if (mounted.current) await refresh(); }
    catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : text('操作失败', 'Action failed')); }
    finally {
      actionPending.current = false;
      if (mounted.current) { setBusy(false); setLoading(false); }
    }
  }

  function startLogin(profile: runtime.RuntimeProfile) {
    if (actionPending.current) return;
    // Open inside the click event, before the request, to avoid popup blockers.
    let popup: Window | null = null;
    try {
      popup = window.open('about:blank', '_blank');
      if (popup) popup.opener = null;
      showLoginPopup(popup, text('正在准备账号授权…', 'Preparing account authorization…'), text('正在请求官方登录地址，准备好后会自动跳转。', 'Requesting the official sign-in URL. This page will redirect when ready.'), english);
    } catch { popup = null; }
    void act(async () => {
      try {
        const next = await api.login(profile.id, profile.runtime === 'cursor' ? 'cursorBrowser' : mode);
        if (!mounted.current) { popup?.close(); return; }
        setAttempt(next);
        if (next.challenge?.url && popup && !popup.closed) popup.location.replace(next.challenge.url);
        else popup?.close();
      } catch (e) {
        const message = e instanceof Error ? e.message : text('登录启动失败', 'Could not start sign-in');
        showLoginPopup(popup, text('暂时无法开始登录', 'Unable to start sign-in'), `${message} ${text('请关闭此页，回到控制台处理后重试。', 'Close this page and return to the console to retry.')}`, english);
        throw e;
      }
    });
  }

  const statusNames: Record<string, string> = {
    ready: text('已连接', 'Connected'), connecting: text('等待登录', 'Awaiting sign-in'),
    disconnected: text('未连接', 'Disconnected'), disconnecting: text('正在断开', 'Disconnecting'),
    error: text('连接异常', 'Connection error'), authorization_required: text('需要超管重新授权', 'Host authorization required'),
  };
  const workerNames: Record<string, string> = {
    warming: text('预热中', 'Warming up'), ready: text('可服务', 'Ready to serve'), busy: text('执行中', 'Working'),
    sleeping: text('等待可用客户端', 'Waiting for a client'), error: text('预热失败，将重试', 'Warmup failed; retrying'),
  };
  const defaultName = text('团队 ', 'Team ') + (engine === 'cursor' ? 'Cursor' : 'Codex');

  return <section className={styles.card} aria-label={text('Agent 引擎连接', 'Agent engine connections')} aria-busy={loading}>
    <header className={styles.heading}>
      <div><h2>{text('Agent 引擎连接', 'Agent engine connections')}</h2>
        <p className={styles.description}>{host
          ? text('连接自己的账号，再按模型授权给可信 admin。', 'Connect your accounts, then grant selected models to trusted admins.')
          : text('Codex / Cursor 由超管连接并授权。DeepAgents 使用下方的模型配置。', 'Your host connects and grants Codex / Cursor. DeepAgents uses the model configuration below.')}</p>
      </div>
      <Button icon={<ArrowsClockwise size={15} />} loading={busy} disabled={loading} onClick={() => void act(async () => {})}>{text('刷新', 'Refresh')}</Button>
    </header>
    {error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} className={styles.notice} />}
    {loading && <div className={styles.loading}><Spin size="small" /><span>{text('正在读取连接状态…', 'Loading connection status…')}</span></div>}
    {!loading && caps && !caps.available && <Alert type="info" showIcon message={text('外部引擎尚未启用', 'External engines are unavailable')} description={caps.reason} />}
    {!loading && caps?.available && <>
      <div className={styles.meta} style={{ marginBottom: 16 }}><Tag>{text('本机执行', 'Local execution')}</Tag>{profilesLoaded && <Tag>{profiles.filter(p => p.status === 'ready').length} / {profiles.length} {text('已连接', 'connected')}</Tag>}</div>
      {profilesLoaded && !profiles.length && <div className={styles.empty}>
        <Plug size={28} /><h3>{text('还没有引擎连接', 'No engine connections yet')}</h3>
        <p>{caps.can_manage_connections ? text('先创建一条连接，再到官方页面登录你的账号。', 'Create a connection below, then sign in on the provider’s official page.') : text('超管授权连接后会显示在这里。', 'Connections appear here after your host grants access.')}</p>
      </div>}
      <div className={styles.list}>{profiles.map(p => <article key={p.id} className={styles.profile} aria-label={p.name}>
        <div className={styles.profileTop}>
          <div><h3 className={styles.profileName}>{p.name}</h3><div className={styles.meta}>
            <Tag>{p.runtime === 'cursor' ? 'Cursor' : 'Codex'}</Tag>
            <Tag color={p.status === 'ready' ? 'green' : p.status === 'error' ? 'error' : undefined}>{statusNames[p.status] || p.status}</Tag>
            <Tag>{host ? text('主机连接', 'Host connection') : text('团队连接', 'Team connection')}</Tag>
          </div></div>
          {p.status === 'ready' && <div className={styles.meta}>
            <Tag color={p.worker?.status === 'ready' ? 'green' : undefined}>{workerNames[p.worker?.status || ''] || text('按需启动', 'Starts on demand')}</Tag>
            {p.capabilities?.web_search && <Tag>{text('网页搜索', 'Web search')}</Tag>}
            {p.capabilities?.image_input && <Tag>{text('图片输入', 'Image input')}</Tag>}
            {p.capabilities?.file_input && <Tag>{text('文件输入', 'File input')}</Tag>}
            {p.capabilities?.image_generation && <Tag>{text('原生生图', 'Image generation')}</Tag>}
          </div>}
        </div>
        {p.account && <p className={styles.account}>{[p.account.email, p.account.planType].filter(Boolean).join(' · ')}</p>}
        {p.recovery_required && <Alert className={styles.notice} type="warning" showIcon message={text('此连接需要主机恢复', 'Host recovery is required')} description={text('上次执行异常退出。请由主机主人检查并停止旧进程，再通过主机恢复命令解除锁定。', 'The previous execution exited unexpectedly. The host owner must check and stop old processes before using the host recovery command.')} />}
        {p.can_manage && <div className={styles.actions}>
          {p.runtime === 'codex' && <Select aria-label={text('Codex 登录方式', 'Codex sign-in method')} value={mode} onChange={setMode} disabled={busy} options={[{ value: 'chatgptDeviceCode', label: text('设备码登录（服务器）', 'Device code (server)') }, { value: 'chatgpt', label: text('浏览器登录（本机）', 'Browser sign-in (local)') }]} />}
          <Button type={p.status === 'ready' ? 'default' : 'primary'} disabled={busy || attempt?.status === 'pending' || p.recovery_required} onClick={() => startLogin(p)}>{p.status === 'ready' ? text('重新登录', 'Sign in again') : text('登录 ', 'Sign in to ') + (p.runtime === 'cursor' ? 'Cursor' : 'Codex')}</Button>
          {p.status === 'ready' && <Button disabled={busy || p.recovery_required} onClick={() => void act(() => api.probe(p.id))}>{text('刷新模型', 'Refresh models')}</Button>}
          {p.status !== 'disconnected' && <Popconfirm title={text('断开此连接？', 'Disconnect this account?')} description={text('此连接的所有任务将停止。', 'All tasks using this connection will stop.')} onConfirm={() => act(() => api.disconnect(p.id))}><Button danger disabled={busy}>{text('断开', 'Disconnect')}</Button></Popconfirm>}
        </div>}
        {attempt?.profile_id === p.id && attempt.status === 'pending' && attempt.challenge && <Alert className={styles.notice} type="info" showIcon message={text('完成账号授权', 'Complete account authorization')} description={<div className={styles.loginChallenge}>
          <Button type="primary" href={attempt.challenge.url} target="_blank" rel="noopener noreferrer">{text('打开官方登录页面', 'Open official sign-in page')}</Button>
          <Typography.Text copyable={{ text: attempt.challenge.url }}>{text('复制登录链接', 'Copy sign-in link')}</Typography.Text>
          {attempt.challenge.user_code && <Typography.Text copyable={{ text: attempt.challenge.user_code }}>{text('设备码：', 'Device code: ')}{attempt.challenge.user_code}</Typography.Text>}
          <Typography.Text type="secondary">{text('登录有效期至 ', 'Valid until ')}{new Date(attempt.expires_at * 1000).toLocaleTimeString(english ? 'en-US' : 'zh-CN')}。{attempt.challenge.mode === 'chatgpt' ? text('请在运行服务器的电脑上完成浏览器回调。', 'Complete the browser callback on the computer running the server.') : text('请在官方页面登录并授权，完成后这里会自动更新。', 'Sign in and authorize on the official page. This console updates automatically.')}</Typography.Text>
          <Button size="small" disabled={busy} onClick={() => void act(() => api.cancelLogin(attempt.profile_id, attempt.id))}>{text('取消登录', 'Cancel sign-in')}</Button>
        </div>} />}
        {p.models.length > 0 && <details className={styles.modelDetails}><summary>{text('可用模型', 'Available models')} · {p.models.length}</summary><div className={styles.modelTags}>{p.models.map(m => <Tag key={m.id}>{m.name}</Tag>)}</div></details>}
        {p.can_manage && <RuntimeGrants profile={p} api={api} revision={grantRevision} />}
      </article>)}</div>
      {caps.can_manage_connections && profilesLoaded && <section className={styles.create} aria-label={text('创建连接', 'Create connection')}>
        <h3>{text('添加一条引擎连接', 'Add an engine connection')}</h3>
        <form className={styles.createForm} onSubmit={event => { event.preventDefault(); if (!busy) void act(() => api.createProfile(name.trim() || defaultName, engine)); }}>
          <label className={styles.field}><span>{text('引擎', 'Engine')}</span><Select aria-label={text('连接引擎', 'Connection engine')} value={engine} disabled={busy} onChange={setEngine} options={[{ value: 'codex', label: 'Codex' }, { value: 'cursor', label: 'Cursor' }]} /></label>
          <label className={styles.field}><span>{text('连接名称', 'Connection name')}</span><Input aria-label={text('连接名称', 'Connection name')} maxLength={80} value={name} disabled={busy} placeholder={defaultName} onChange={e => setName(e.target.value)} /></label>
          <Button type="primary" icon={<Plus size={15} />} htmlType="submit" disabled={busy}>{text('创建连接', 'Create connection')}</Button>
        </form>
      </section>}
      {!host && profilesLoaded && <RuntimeDefault profiles={profiles} />}
    </>}
  </section>;
}

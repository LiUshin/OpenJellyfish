import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Popconfirm, Select, Spin, Tag } from 'antd';
import * as runtime from '../services/runtime';
import type { HostClient } from '../services/superadmin';
import { useRuntimeCopy } from './runtimeManagementCopy';
import styles from './runtimeManagement.module.css';

export default function RuntimeGrants({ profile, api, revision = 0 }: { profile: runtime.RuntimeProfile; api: HostClient; revision?: number }) {
  const { text } = useRuntimeCopy();
  const [admins, setAdmins] = useState<{ id: string; username: string }[]>([]);
  const [grants, setGrants] = useState<runtime.RuntimeGrant[]>([]);
  const [actor, setActor] = useState<string>();
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const mounted = useRef(false);
  const actionPending = useRef(false);
  const readVersion = useRef(0);
  const scope = useRef({ profileId: profile.id, authGeneration: profile.auth_generation, api });
  scope.current = { profileId: profile.id, authGeneration: profile.auth_generation, api };

  useEffect(() => {
    let disposed = false;
    mounted.current = true;
    setLoading(true);
    setLoaded(false);
    setError('');
    setSaved(false);
    const version = ++readVersion.current;
    Promise.all([api.admins(), api.grants(profile.id)]).then(([a, g]) => {
      if (!disposed && version === readVersion.current) { setAdmins(a); setGrants(g); setLoaded(true); }
    }).catch(e => {
      if (!disposed && version === readVersion.current) setError(e instanceof Error ? e.message : String(e));
    }).finally(() => { if (!disposed && version === readVersion.current) setLoading(false); });
    return () => { disposed = true; mounted.current = false; readVersion.current++; };
  }, [profile.id, profile.auth_generation, api, reload, revision]);

  async function act(fn: () => Promise<unknown>) {
    if (actionPending.current) return;
    actionPending.current = true;
    const mutationScope = scope.current;
    const isCurrentScope = () => mounted.current
      && scope.current.profileId === mutationScope.profileId
      && scope.current.authGeneration === mutationScope.authGeneration
      && scope.current.api === mutationScope.api;
    let receiptVersion: number | undefined;
    setBusy(true);
    setError('');
    setSaved(false);
    try {
      await fn();
      if (!isCurrentScope()) return;
      // A refresh during the write may have read the old grants. Start a new read
      // after the receipt so that earlier snapshots cannot replace this result.
      const version = ++readVersion.current;
      receiptVersion = version;
      const [currentAdmins, currentGrants] = await Promise.all([api.admins(), api.grants(profile.id)]);
      if (isCurrentScope() && version === readVersion.current) {
        setAdmins(currentAdmins);
        setGrants(currentGrants);
        setLoaded(true);
        setLoading(false);
        setSaved(true);
      }
    } catch (e) {
      if (isCurrentScope() && (receiptVersion === undefined || receiptVersion === readVersion.current)) {
        setError(e instanceof Error ? e.message : text('授权修改失败', 'Could not update grants'));
        setLoading(false);
      }
    } finally {
      actionPending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  const selectedModels = models.filter(id => profile.models.some(m => m.id === id));
  const canSave = loaded && !busy && !loading && !!actor && admins.some(a => a.id === actor) && selectedModels.length > 0 && profile.status === 'ready' && !profile.recovery_required;
  const modelName = (id: string) => profile.models.find(m => m.id === id)?.name || id;

  return <section className={styles.grants} aria-label={text('共享给可信 admin', 'Share with trusted admins')} aria-busy={loading}>
    <h3>{text('共享给可信 admin', 'Share with trusted admins')}</h3>
    <p className={styles.description}>{text('选择成员和可用模型。共享账号额度，聊天与文件目录各自保留。', 'Choose members and their allowed models. Account quota is shared; conversations and file directories remain separate.')}</p>
    {error && <Alert type="error" showIcon message={error} className={styles.notice} action={!loaded && <Button size="small" disabled={loading || busy} onClick={() => setReload(value => value + 1)}>{text('重试', 'Retry')}</Button>} />}
    {saved && <Alert type="success" showIcon message={text('授权已更新', 'Grants updated')} className={styles.notice} closable onClose={() => setSaved(false)} />}
    {loading && <div className={styles.loading}><Spin size="small" /><span>{text('正在读取成员与授权…', 'Loading members and grants…')}</span></div>}
    {!loading && loaded && <>
      {grants.map(g => <div key={g.id} className={styles.grantRow}>
        <div className={styles.grantTop}>
          <div className={styles.grantWho}>
            <strong>{admins.find(a => a.id === g.actor_id)?.username || g.actor_id}</strong>
            <Tag color={g.enabled && g.auth_generation === profile.auth_generation ? 'green' : undefined}>{!g.enabled ? text('已撤销', 'Revoked') : g.auth_generation !== profile.auth_generation ? text('账号已变更，需重新授权', 'Account changed; renew grant') : text('已授权', 'Granted')}</Tag>
          </div>
          <div className={styles.grantActions}>
            <Button size="small" disabled={busy} onClick={() => { setActor(g.actor_id); setModels(g.models.filter(m => profile.models.some(p => p.id === m))); setSaved(false); }}>{text('编辑授权', 'Edit grant')}</Button>
            {g.enabled && <Popconfirm title={text('撤销此成员的授权？', 'Revoke this member’s access?')} description={text('此 admin 使用该连接的任务将停止。', 'This admin’s tasks using the connection will stop.')} onConfirm={() => act(() => api.revoke(profile.id, g.id))}><Button size="small" danger disabled={busy}>{text('撤销', 'Revoke')}</Button></Popconfirm>}
          </div>
        </div>
        <div className={styles.modelTags}>{g.models.map(id => <Tag key={id}>{modelName(id)}</Tag>)}</div>
      </div>)}
      {!admins.length && <Alert className={styles.notice} type="info" showIcon message={text('还没有可授权的 admin', 'No admins to grant access to yet')} description={text('先在桌面客户端创建注册码，让团队成员完成注册，再刷新此页。', 'Create a registration code in the desktop app and have your team member register, then refresh this page.')} />}
      {admins.length > 0 && !grants.length && <p className={styles.description} style={{ marginTop: 12 }}>{text('还没有成员获授权。连接后，在下方添加第一位成员。', 'No members have access yet. Once connected, add the first grant below.')}</p>}
      {admins.length > 0 && <form className={styles.grantForm} onSubmit={event => { event.preventDefault(); if (canSave && actor) void act(() => api.grant(profile.id, actor, selectedModels)); }}>
        <label className={styles.field}><span>{text('团队成员', 'Team member')}</span><Select aria-label={text('选择可信 admin', 'Choose a trusted admin')} placeholder={text('选择 admin', 'Select an admin')} value={actor} disabled={busy} onChange={value => { setActor(value); setSaved(false); }} showSearch optionFilterProp="label" options={admins.map(a => ({ value: a.id, label: a.username }))} /></label>
        <label className={styles.field}><span>{text('可用模型', 'Allowed models')}</span><Select aria-label={text('授权模型', 'Granted models')} mode="multiple" placeholder={text('允许使用的模型', 'Choose allowed models')} value={selectedModels} disabled={busy || profile.status !== 'ready'} onChange={value => { setModels(value); setSaved(false); }} maxTagCount="responsive" optionFilterProp="label" options={profile.models.map(m => ({ value: m.id, label: m.name }))} /></label>
        <Button type="primary" htmlType="submit" disabled={!canSave} loading={busy}>{text('保存授权', 'Save grant')}</Button>
      </form>}
      {profile.status !== 'ready' && admins.length > 0 && <p className={styles.description} style={{ marginTop: 10 }}>{text('先完成账号登录，才能选择模型并保存授权。', 'Complete account sign-in before choosing models and saving a grant.')}</p>}
    </>}
  </section>;
}

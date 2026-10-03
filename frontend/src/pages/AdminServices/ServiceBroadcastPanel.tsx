import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Collapse, Input, List, Popconfirm, Select, Space, Tag, Typography, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import * as api from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';
import { createRequestKey } from '../../utils/requestKey';

export default function ServiceBroadcastPanel({ serviceId, published, externalEngine }: { serviceId: string; published: boolean; externalEngine: boolean }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [mode, setMode] = useState('notice');
  const [conversations, setConversations] = useState<api.ServiceConvSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState('');
  const [sendAt, setSendAt] = useState('');
  const [scheduled, setScheduled] = useState(false);
  const [broadcasts, setBroadcasts] = useState<api.ServiceBroadcast[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  const requestBusy = useRef(false);
  const pendingKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const load = useCallback(async (silent = false) => {
    if (silent && requestBusy.current) return;
    requestBusy.current = true;
    const seq = ++sequence.current;
    try {
      const [recipients, history] = await Promise.all([api.listServiceConversations(serviceId), api.listServiceBroadcasts(serviceId)]);
      if (seq !== sequence.current) return;
      setConversations(recipients); setBroadcasts(history); setError('');
    } catch (e) { if (seq === sequence.current) setError(e instanceof Error ? e.message : t('inbox.opFail')); }
    finally { if (seq === sequence.current) requestBusy.current = false; }
  }, [serviceId, t]);
  useEffect(() => {
    void load();
    const refresh = () => { if (!document.hidden) void load(true); };
    const timer = setInterval(refresh, 5000);
    window.addEventListener('focus', refresh);
    return () => { ++sequence.current; clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, [load]);

  const send = async () => {
    if (busy || !selected.length || !text.trim() || !published) return;
    let at: string | undefined;
    if (scheduled) {
      const date = new Date(sendAt);
      if (!sendAt || !Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) { message.error(t('serviceMessaging.futureRequired')); return; }
      at = date.toISOString();
    }
    const body = { message: text.trim(), conversation_ids: [...selected].sort(), ...(at ? { scheduled_at: at } : {}) };
    const fingerprint = JSON.stringify(body);
    if (pendingKey.current?.fingerprint !== fingerprint) pendingKey.current = { fingerprint, key: createRequestKey() };
    setBusy(true);
    try {
      await api.createServiceBroadcast(serviceId, { ...body, idempotency_key: pendingKey.current.key });
      message.success(t('serviceMessaging.broadcastQueued'));
      setText(''); pendingKey.current = null;
      void load();
    } catch (e) { message.error(e instanceof Error ? e.message : t('inbox.opFail')); }
    finally { setBusy(false); }
  };
  const manage = async (item: api.ServiceBroadcast, action: 'cancel' | 'retry', allowUnknown = false) => {
    if (busy) return;
    setBusy(true);
    try { await api.manageServiceBroadcast(serviceId, item.id, action, allowUnknown); void load(); }
    catch (e) { message.error(e instanceof Error ? e.message : t('inbox.opFail')); }
    finally { setBusy(false); }
  };

  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Select aria-label={t('serviceMessaging.broadcastMode')} value={mode} onChange={setMode} style={{ width: '100%' }} options={[{ value: 'notice', label: t('serviceMessaging.directNotice') }, { value: 'task', label: t('serviceMessaging.agentTask') }]} />
    {mode === 'task' ? <>
      <Alert type="info" showIcon message={t(externalEngine ? 'serviceMessaging.runtimeTaskUnavailable' : 'serviceMessaging.agentTaskHint')} />
      <Button disabled={externalEngine} onClick={() => navigate('/settings/scheduler')}>{t('serviceMessaging.openScheduler')}</Button>
    </> : <>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>{t('serviceMessaging.noticeHint')}</Typography.Paragraph>
      {!published && <Alert type="warning" message={t('serviceMessaging.unpublished')} />}
      {error && <Alert type="error" message={error} action={<Button onClick={() => void load()}>{t('inbox.refresh')}</Button>} />}
      <label htmlFor="broadcast-recipients">{t('serviceMessaging.recipients')}</label>
      <Select id="broadcast-recipients" mode="multiple" disabled={busy} value={selected} onChange={setSelected} style={{ width: '100%' }} maxTagCount="responsive" optionFilterProp="label" placeholder={t('serviceMessaging.chooseRecipients')} options={conversations.map((c) => ({ value: c.id, label: `[${c.source || 'api'}] ${c.title || c.id}` }))} />
      <Typography.Text type="secondary">{t('serviceMessaging.recipientCount', { count: selected.length })}</Typography.Text>
      <label htmlFor="broadcast-text">{t('serviceMessaging.noticeContent')}</label>
      <Input.TextArea id="broadcast-text" value={text} disabled={busy} onChange={(e) => setText(e.target.value)} autoSize={{ minRows: 3, maxRows: 8 }} maxLength={8000} showCount />
      <Space wrap>
        <Select aria-label={t('serviceMessaging.timing')} value={scheduled ? 'scheduled' : 'now'} onChange={(v) => setScheduled(v === 'scheduled')} options={[{ value: 'now', label: t('serviceMessaging.now') }, { value: 'scheduled', label: t('serviceMessaging.schedule') }]} />
        {scheduled && <input aria-label={t('serviceMessaging.sendAt')} type="datetime-local" value={sendAt} onChange={(e) => setSendAt(e.target.value)} style={{ background: 'var(--jf-bg-panel)', color: 'var(--jf-text)', padding: 6, border: '1px solid var(--jf-border)', borderRadius: 6 }} />}
        <Button type="primary" disabled={!published || !selected.length || !text.trim()} loading={busy} onClick={() => void send()}>{t('serviceMessaging.queueNotice')}</Button>
      </Space>
    </>}
    <List dataSource={broadcasts} rowKey="id" locale={{ emptyText: t('serviceMessaging.noBroadcasts') }} renderItem={(item) => {
      const unknown = item.deliveries.some((d) => d.status === 'unknown');
      const retryable = item.deliveries.some((d) => d.status === 'retry_wait');
      const cancellable = item.deliveries.some((d) => ['pending', 'retry_wait'].includes(d.status));
      const completed = item.messages.filter((m) => { const ds = item.deliveries.filter((d) => d.message_id === m.id); return ds.length > 0 && ds.every((d) => d.status === 'delivered'); }).length;
      return <List.Item style={{ display: 'block' }}>
        <Typography.Paragraph ellipsis={{ rows: 2, expandable: true }} style={{ whiteSpace: 'pre-wrap' }}>{item.content}</Typography.Paragraph>
        <Space wrap><Typography.Text type="secondary">{fmtUserTime(item.scheduled_at || item.created_at, 'short')}</Typography.Text><Tag>{t('serviceMessaging.deliveryCount', { done: completed, total: item.recipient_count })}</Tag>
          {retryable && !unknown && <Button size="small" disabled={busy} onClick={() => void manage(item, 'retry')}>{t('serviceMessaging.retry')}</Button>}
          {unknown && <Popconfirm title={t('serviceMessaging.unknownRetry')} onConfirm={() => void manage(item, 'retry', true)}><Button size="small" disabled={busy}>{t('serviceMessaging.resend')}</Button></Popconfirm>}
          {cancellable && <Button size="small" disabled={busy} onClick={() => void manage(item, 'cancel')}>{t('serviceMessaging.cancelPending')}</Button>}
        </Space>
        <Collapse ghost size="small" items={[{ key: item.id, label: t('serviceMessaging.deliveryDetails'), children: <List size="small" dataSource={item.messages} rowKey="id" renderItem={(m) => <List.Item style={{ display: 'block' }}><Typography.Text code>{m.conversation_id}</Typography.Text>{item.deliveries.filter((d) => d.message_id === m.id).map((d) => <div key={d.id}><Tag>{d.channel === 'web' ? t('serviceMessaging.conversationHistory') : t('serviceMessaging.wechatDelivery')}</Tag><Tag color={d.status === 'delivered' ? 'green' : d.status === 'unknown' ? 'orange' : undefined}>{t(`serviceMessaging.delivery.${d.status}`)}</Tag>{d.error && <Typography.Text type="secondary">{d.error}</Typography.Text>}</div>)}</List.Item>} /> }]} />
      </List.Item>;
    }} />
  </Space>;
}

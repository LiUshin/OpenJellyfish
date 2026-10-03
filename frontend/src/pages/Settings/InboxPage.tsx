import SettingsLoadError from '../../components/SettingsLoadError';
import { useRef, useState, useEffect, useCallback } from 'react';
import { List, Tag, Button, Empty, Spin, Typography, Popconfirm, message, Badge, Drawer, Input, Alert, Space } from 'antd';
import { Envelope, ArrowClockwise } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import * as api from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';
import { useIsMobile } from '../../hooks/useMediaQuery';
import { createRequestKey } from '../../utils/requestKey';

const { Text, Paragraph } = Typography;
const notifyChanged = () => window.dispatchEvent(new Event('inbox-changed'));

export default function InboxPage() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [items, setItems] = useState<api.InboxMessage[]>([]);
  const [unread, setUnread] = useState(0);
  const [loadError, setLoadError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>();
  const [selected, setSelected] = useState<api.InboxMessage | null>(null);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [actionPending, setActionPending] = useState(false);
  const [context, setContext] = useState<api.ServiceConvDetail | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const requestSequence = useRef(0);
  const requestBusy = useRef(false);
  const detailSequence = useRef(0);
  const selectedId = useRef<string | null>(null);
  const requestKey = useRef<{ text: string; key: string } | null>(null);
  useEffect(() => () => { ++requestSequence.current; ++detailSequence.current; selectedId.current = null; }, []);

  const load = useCallback(async (silent = false) => {
    if (silent && requestBusy.current) return;
    requestBusy.current = true;
    const sequence = ++requestSequence.current;
    const detailId = selectedId.current;
    if (!silent) setLoading(true);
    try {
      const [res, detail] = await Promise.all([
        api.listInbox(filter),
        detailId ? api.getInboxMessage(detailId).catch(() => null) : Promise.resolve(null),
      ]);
      if (sequence !== requestSequence.current) return;
      setLoadError(false);
      setItems(res.messages);
      setUnread(res.unread_count);
      setSelected((prev) => prev?.id === detailId ? detail || res.messages.find((m) => m.id === detailId) || prev : prev);
    } catch { if (sequence === requestSequence.current) setLoadError(true); }
    finally { if (sequence === requestSequence.current) { requestBusy.current = false; setLoading(false); } }
  }, [filter]);

  useEffect(() => {
    void load();
    const refresh = () => { if (!document.hidden) void load(true); };
    const timer = setInterval(refresh, 5000);
    window.addEventListener('focus', refresh);
    return () => { ++requestSequence.current; clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, [load]);

  const closeDetail = () => {
    ++detailSequence.current;
    selectedId.current = null;
    setSelected(null);
    setContext(null);
    setContextLoading(false);
    setDraft('');
    requestKey.current = null;
  };
  const openDetail = (item: api.InboxMessage) => {
    ++detailSequence.current;
    selectedId.current = item.id;
    setSelected(item);
    setDraft('');
    setContext(null);
    setContextLoading(false);
    requestKey.current = null;
    if (item.status === 'unread') {
      void api.updateInboxStatus(item.id, 'read').then(() => { notifyChanged(); void load(); }).catch(() => message.error(t('inbox.opFail')));
    }
  };
  const showContext = async () => {
    if (!selected) return;
    const id = selected.id;
    const sequence = ++detailSequence.current;
    setContextLoading(true);
    try {
      const result = await api.getServiceConversation(selected.service_id, selected.conversation_id);
      if (selectedId.current === id && sequence === detailSequence.current) setContext(result);
    } catch { if (selectedId.current === id) message.error(t('serviceMessaging.contextFailed')); }
    finally { if (selectedId.current === id && sequence === detailSequence.current) setContextLoading(false); }
  };
  const sendReply = async () => {
    const content = draft.trim();
    if (!selected || !content || sending) return;
    const id = selected.id;
    if (!requestKey.current || requestKey.current.text !== content) requestKey.current = { text: content, key: createRequestKey() };
    setSending(true);
    try {
      const result = await api.replyInbox(id, content, requestKey.current.key);
      if (selectedId.current === id) {
        setSelected(result.case);
        setDraft('');
        requestKey.current = null;
      }
      message.success(t('serviceMessaging.replyQueued'));
      notifyChanged();
      void load();
    } catch (error) { message.error(error instanceof Error ? error.message : t('inbox.opFail')); }
    finally { setSending(false); }
  };
  const retry = async (delivery: api.MessageDelivery) => {
    if (!selected || actionPending) return;
    setActionPending(true);
    try {
      await api.retryInboxDelivery(selected.id, delivery.id, delivery.status === 'unknown');
      message.success(t('serviceMessaging.retryQueued'));
      void load();
    } catch (error) { message.error(error instanceof Error ? error.message : t('inbox.opFail')); }
    finally { setActionPending(false); }
  };
  const resolve = async () => {
    if (!selected || actionPending) return;
    const id = selected.id;
    setActionPending(true);
    try {
      const result = await api.resolveInbox(id);
      if (selectedId.current === id) setSelected(result);
      notifyChanged(); void load();
    } catch { message.error(t('inbox.opFail')); }
    finally { setActionPending(false); }
  };
  const remove = async (id: string) => {
    try {
      await api.deleteInboxMessage(id);
      if (selectedId.current === id) closeDetail();
      notifyChanged(); void load();
    } catch { message.error(t('inbox.deleteFail')); }
  };
  const deliveryLabel = (d: api.MessageDelivery) => t(`serviceMessaging.delivery.${d.status}`);
  const deliveries = selected?.deliveries || (selected?.notification ? [selected.notification] : []);

  return (
    <div className="settings-page inbox-settings">
      <div className="settings-toolbar" style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 20 }}>
        <Space><Text strong style={{ fontSize: 18 }}>{t('inbox.title')}</Text><Badge count={unread} /></Space>
        <Space wrap>
          {(['all', 'unread', 'handled'] as const).map((f) => <Button key={f} size="small" type={(filter || 'all') === f ? 'primary' : 'default'} aria-pressed={(filter || 'all') === f} onClick={() => setFilter(f === 'all' ? undefined : f)}>{t(`inbox.${f === 'all' ? 'filterAll' : f === 'unread' ? 'filterUnread' : 'filterHandled'}`)}</Button>)}
          <Button aria-label={t('inbox.refresh')} icon={<ArrowClockwise />} onClick={() => void load()} />
        </Space>
      </div>
      {loadError && <SettingsLoadError onRetry={() => void load()} />}
      <div className="settings-list-panel"><Spin spinning={loading}>
        {!items.length && !loading && !loadError ? <Empty description={t('inbox.empty')} /> : <List dataSource={items} rowKey="id" renderItem={(item) => (
          <List.Item actions={[
            <Button key="open" onClick={() => openDetail(item)}>{t('serviceMessaging.viewReply')}</Button>,
            <Popconfirm key="delete" title={t('inbox.deleteConfirm')} onConfirm={() => void remove(item.id)}><Button danger type="text">{t('inbox.deleteBtn')}</Button></Popconfirm>,
          ]}>
            <List.Item.Meta avatar={<Envelope weight={item.status === 'unread' ? 'fill' : 'regular'} size={20} />} title={<Space wrap><Text strong>{item.service_name}</Text><Tag>{item.channel || (item.wechat_session_id ? 'wechat' : 'api')}</Tag><Tag color={item.case_status === 'resolved' ? 'green' : 'blue'}>{t(`serviceMessaging.case.${item.case_status || 'open'}`)}</Tag></Space>} description={<><Paragraph ellipsis={{ rows: 2 }} style={{ marginBottom: 4 }}>{item.message}</Paragraph><Text type="secondary">{fmtUserTime(item.timestamp, 'short')}</Text>{item.notification && <div>{t('serviceMessaging.adminNotification')}: {deliveryLabel(item.notification)}</div>}</>} />
          </List.Item>
        )} />}
      </Spin></div>
      <Drawer title={selected?.service_name || t('inbox.title')} open={!!selected} onClose={closeDetail} width={isMobile ? '100%' : 560} destroyOnClose>
        {selected && <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap><Tag>{selected.channel || 'api'}</Tag><Tag>{t(`serviceMessaging.case.${selected.case_status || 'open'}`)}</Tag><Text type="secondary">{selected.conversation_id}</Text></Space>
          <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{selected.message}</Paragraph>
          <Button loading={contextLoading} onClick={() => void showContext()}>{t('serviceMessaging.viewContext')}</Button>
          {context && <div style={{ maxHeight: 300, overflowY: 'auto', padding: 12, background: 'var(--jf-bg-raised)', borderRadius: 8 }}>{context.messages.map((m, i) => <div key={i}><Text strong>{m.role}</Text><Paragraph style={{ whiteSpace: 'pre-wrap' }}>{m.content}</Paragraph></div>)}</div>}
          {(selected.messages || []).filter((m) => m.author_type === 'admin').map((m) => <div key={m.id}><Text strong>{t('serviceMessaging.adminReply')}</Text><Paragraph style={{ whiteSpace: 'pre-wrap' }}>{m.content}</Paragraph></div>)}
          {deliveries.map((d) => <div key={d.id} style={{ padding: 10, border: '1px solid var(--jf-border)', borderRadius: 8 }}>
            <Space wrap><Text>{d.channel === 'admin_wechat' ? t('serviceMessaging.adminNotification') : d.channel === 'web' ? t('serviceMessaging.conversationHistory') : t('serviceMessaging.wechatDelivery')}</Text><Tag color={d.status === 'delivered' ? 'green' : d.status === 'unknown' ? 'orange' : 'default'}>{deliveryLabel(d)}</Tag></Space>
            {d.error && <Paragraph type="secondary" style={{ margin: '6px 0', overflowWrap: 'anywhere' }}>{d.error}</Paragraph>}
            {d.status === 'retry_wait' && <Button size="small" loading={actionPending} onClick={() => void retry(d)}>{t('serviceMessaging.retry')}</Button>}
            {d.status === 'unknown' && <Popconfirm title={t('serviceMessaging.unknownRetry')} onConfirm={() => void retry(d)}><Button size="small" loading={actionPending}>{t('serviceMessaging.resend')}</Button></Popconfirm>}
          </div>)}
          <Alert type="info" showIcon message={t('serviceMessaging.replyHint')} />
          <label htmlFor="inbox-reply">{t('serviceMessaging.reply')}</label>
          <Input.TextArea id="inbox-reply" value={draft} disabled={sending} maxLength={8000} showCount autoSize={{ minRows: 3, maxRows: 10 }} onChange={(e) => setDraft(e.target.value)} placeholder={t('serviceMessaging.replyPlaceholder')} />
          <Space wrap><Button type="primary" loading={sending} disabled={!draft.trim()} onClick={() => void sendReply()}>{t('serviceMessaging.sendReply')}</Button><Button loading={actionPending} disabled={selected.case_status === 'resolved' || sending} onClick={() => void resolve()}>{t('serviceMessaging.resolve')}</Button></Space>
        </Space>}
      </Drawer>
    </div>
  );
}

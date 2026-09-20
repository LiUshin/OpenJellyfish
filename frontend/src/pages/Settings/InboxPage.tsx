import SettingsLoadError from '../../components/SettingsLoadError';
import { useRef, useState, useEffect, useCallback, useMemo } from 'react';
import { List, Tag, Button, Empty, Spin, Typography, Popconfirm, message, Badge, Tooltip, Collapse } from 'antd';
import {
  Envelope, EnvelopeOpen, Trash, Eye, Robot, ArrowClockwise, User,
} from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import * as api from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';
import { useIsMobile } from '../../hooks/useMediaQuery';

const { Text, Paragraph } = Typography;

const STATUS_KEYS: Record<string, { color: string; key: string }> = {
  unread: { color: 'red', key: 'inbox.statusUnread' },
  read: { color: 'default', key: 'inbox.statusRead' },
  handled: { color: 'green', key: 'inbox.statusHandled' },
};

export default function InboxPage() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [messages, setMessages] = useState<api.InboxMessage[]>([]);
  const [loadError, setLoadError] = useState(false);
  const requestSequence = useRef(0);
  useEffect(() => () => { ++requestSequence.current; }, []);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string | undefined>(undefined);

  const load = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setLoadError(false);
    setLoading(true);
    try {
      const res = await api.listInbox(filter);
      if (sequence !== requestSequence.current) return;
      setMessages(res.messages);
    } catch {
      if (sequence === requestSequence.current) setLoadError(true);
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [filter, t]);

  useEffect(() => { load(); }, [load]);

  const handleMarkRead = async (id: string) => {
    try {
      await api.updateInboxStatus(id, 'read');
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, status: 'read' } : m)));
    } catch {
      message.error(t('inbox.opFail'));
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteInboxMessage(id);
      setMessages((prev) => prev.filter((m) => m.id !== id));
      message.success(t('inbox.deleted'));
    } catch {
      message.error(t('inbox.deleteFail'));
    }
  };

  const filterLabels = useMemo(() => ({
    all: t('inbox.filterAll'),
    unread: t('inbox.filterUnread'),
    handled: t('inbox.filterHandled'),
  }), [t]);

  const unreadCount = messages.filter((m) => m.status === 'unread').length;

  return (
    <div className="settings-page inbox-settings">
      <div className="settings-toolbar" style={{
        display: 'flex',
        alignItems: isMobile ? 'flex-start' : 'center',
        justifyContent: 'space-between',
        marginBottom: 20,
        flexWrap: isMobile ? 'wrap' : 'nowrap',
        gap: isMobile ? 12 : 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Text style={{ fontSize: 18, fontWeight: 600, color: 'var(--jf-text)' }}>{t('inbox.title')}</Text>
          {unreadCount > 0 && (
            <Badge count={unreadCount} style={{ backgroundColor: '#e8524a' }} />
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {(['all', 'unread', 'handled'] as const).map((f) => (
            <Button
              key={f}
              aria-pressed={(filter === undefined && f === 'all') || filter === f}
              size="small"
              type={(filter === undefined && f === 'all') || filter === f ? 'primary' : 'default'}
              onClick={() => setFilter(f === 'all' ? undefined : f)}
              style={{ borderRadius: 'var(--jf-radius-sm)' }}
            >
              {filterLabels[f]}
            </Button>
          ))}
          <Tooltip title={t('inbox.refresh')}>
            <Button aria-label={t('inbox.refresh')} type="text" size="small" icon={<ArrowClockwise size={16} />} onClick={load} style={{ color: 'var(--jf-text-muted)' }} />
          </Tooltip>
        </div>
      </div>

      {loadError && <SettingsLoadError onRetry={() => void load()} />}
      <div className="settings-list-panel"><Spin spinning={loading}>
        {messages.length === 0 && !loading && !loadError ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<Text style={{ color: 'var(--jf-text-muted)' }}>{t('inbox.empty')}</Text>}
            style={{ marginTop: 60 }}
          />
        ) : (
          <List
            dataSource={messages}
            renderItem={(item) => {
              const st = STATUS_KEYS[item.status] || STATUS_KEYS.unread;
              const time = fmtUserTime(item.timestamp, 'short');
              return (
                <div
                  key={item.id}
                  style={{
                    padding: '14px 16px',
                    marginBottom: 8,
                    background: item.status === 'unread' ? 'rgba(var(--jf-primary-rgb), 0.04)' : 'var(--jf-bg-panel)',
                    borderRadius: 'var(--jf-radius-md)',
                    border: '1px solid var(--jf-border)',
                  }}
                >
                  <div style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'flex-start',
                    marginBottom: 8,
                    flexWrap: isMobile ? 'wrap' : 'nowrap',
                    gap: isMobile ? 6 : 0,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flexWrap: 'wrap' }}>
                      {item.status === 'unread' ? (
                        <Envelope size={18} weight="fill" style={{ color: '#e8524a', flexShrink: 0 }} />
                      ) : (
                        <EnvelopeOpen size={18} style={{ color: 'var(--jf-text-muted)', flexShrink: 0 }} />
                      )}
                      <Text style={{
                        color: 'var(--jf-text)', fontWeight: 500,
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                        maxWidth: isMobile ? '55vw' : 'unset',
                      }}>
                        {item.service_name}
                      </Text>
                      {item.wechat_user_id && (
                        <Tooltip title={item.wechat_session_id
                          ? t('inbox.wechatTipWithSession', { id: item.wechat_user_id, session: item.wechat_session_id })
                          : t('inbox.wechatTip', { id: item.wechat_user_id })}>
                          <Tag
                            icon={<User size={11} style={{ marginRight: 3, verticalAlign: -1 }} />}
                            color="cyan"
                            style={{ fontSize: 11, cursor: 'default' }}
                          >
                            {item.wechat_user_id.length > 12
                              ? item.wechat_user_id.slice(0, 6) + '…' + item.wechat_user_id.slice(-4)
                              : item.wechat_user_id}
                          </Tag>
                        </Tooltip>
                      )}
                      <Tag color={st.color} style={{ marginLeft: 4, fontSize: 11 }}>{t(st.key)}</Tag>
                      {item.handled_by === 'agent' && (
                        <Tooltip title={t('inbox.tagAgentHandled')}>
                          <Robot size={14} style={{ color: '#52c41a', flexShrink: 0 }} />
                        </Tooltip>
                      )}
                    </div>
                    <Text style={{
                      color: 'var(--jf-text-muted)', fontSize: 12,
                      flexShrink: 0,
                      marginLeft: isMobile ? 0 : 12,
                    }}>{time}</Text>
                  </div>

                  <Paragraph
                    style={{ color: 'var(--jf-text)', fontSize: 13, marginBottom: item.agent_response ? 8 : 4, whiteSpace: 'pre-wrap' }}
                    ellipsis={{ rows: 3, expandable: true, symbol: t('inbox.expand') }}
                  >
                    {item.message}
                  </Paragraph>

                  {item.agent_response && (
                    <Collapse
                      size="small"
                      ghost
                      items={[{
                        key: '1',
                        label: <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>{t('inbox.agentResultLabel')}</Text>,
                        children: (
                          <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 12, margin: 0, whiteSpace: 'pre-wrap' }}>
                            {item.agent_response}
                          </Paragraph>
                        ),
                      }]}
                    />
                  )}

                  <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end', marginTop: 4 }}>
                    {item.status === 'unread' && (
                      <Tooltip title={t('inbox.markRead')}>
                        <Button aria-label={t('inbox.markRead')} type="text" size="small" icon={<Eye size={14} />} onClick={() => handleMarkRead(item.id)} style={{ color: 'var(--jf-text-muted)' }} />
                      </Tooltip>
                    )}
                    <Popconfirm title={t('inbox.deleteConfirm')} onConfirm={() => handleDelete(item.id)} okText={t('inbox.deleteBtn')} cancelText={t('common.cancel')}>
                      <Tooltip title={t('inbox.deleteBtn')}>
                        <Button aria-label={t('inbox.deleteBtn')} type="text" size="small" danger icon={<Trash size={14} />} />
                      </Tooltip>
                    </Popconfirm>
                  </div>
                </div>
              );
            }}
          />
        )}
      </Spin></div>
    </div>
  );
}

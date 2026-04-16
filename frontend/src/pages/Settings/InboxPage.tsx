import { useState, useEffect, useCallback } from 'react';
import { List, Tag, Button, Empty, Spin, Typography, Popconfirm, message, Badge, Tooltip, Collapse } from 'antd';
import {
  Envelope, EnvelopeOpen, Trash, Eye, Robot, ArrowClockwise, User,
} from '@phosphor-icons/react';
import * as api from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';

const { Text, Paragraph } = Typography;

const STATUS_TAGS: Record<string, { color: string; label: string }> = {
  unread: { color: 'red', label: '未读' },
  read: { color: 'default', label: '已读' },
  handled: { color: 'green', label: '已处理' },
};

export default function InboxPage() {
  const [messages, setMessages] = useState<api.InboxMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string | undefined>(undefined);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listInbox(filter);
      setMessages(res.messages);
    } catch {
      message.error('加载收件箱失败');
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const handleMarkRead = async (id: string) => {
    try {
      await api.updateInboxStatus(id, 'read');
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, status: 'read' } : m)));
    } catch {
      message.error('操作失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteInboxMessage(id);
      setMessages((prev) => prev.filter((m) => m.id !== id));
      message.success('已删除');
    } catch {
      message.error('删除失败');
    }
  };

  const unreadCount = messages.filter((m) => m.status === 'unread').length;

  return (
    <div style={{ padding: '24px 32px', maxWidth: 960, margin: '0 auto', width: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Text style={{ fontSize: 18, fontWeight: 600, color: '#e0e0e8' }}>收件箱</Text>
          {unreadCount > 0 && (
            <Badge count={unreadCount} style={{ backgroundColor: '#e8524a' }} />
          )}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {(['all', 'unread', 'handled'] as const).map((f) => (
            <Button
              key={f}
              size="small"
              type={(filter === undefined && f === 'all') || filter === f ? 'primary' : 'default'}
              onClick={() => setFilter(f === 'all' ? undefined : f)}
              style={{ borderRadius: 'var(--jf-radius-sm)' }}
            >
              {f === 'all' ? '全部' : f === 'unread' ? '未读' : '已处理'}
            </Button>
          ))}
          <Tooltip title="刷新">
            <Button type="text" size="small" icon={<ArrowClockwise size={16} />} onClick={load} style={{ color: 'var(--jf-text-muted)' }} />
          </Tooltip>
        </div>
      </div>

      <Spin spinning={loading}>
        {messages.length === 0 && !loading ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<Text style={{ color: 'var(--jf-text-muted)' }}>暂无消息</Text>}
            style={{ marginTop: 60 }}
          />
        ) : (
          <List
            dataSource={messages}
            renderItem={(item) => {
              const st = STATUS_TAGS[item.status] || STATUS_TAGS.unread;
              const time = fmtUserTime(item.timestamp, 'short');
              return (
                <div
                  key={item.id}
                  style={{
                    padding: '14px 16px',
                    marginBottom: 8,
                    background: item.status === 'unread' ? 'rgba(232, 82, 74, 0.06)' : 'rgba(255,255,255,0.03)',
                    borderRadius: 'var(--jf-radius-md)',
                    border: `1px solid ${item.status === 'unread' ? 'rgba(232, 82, 74, 0.15)' : 'rgba(255,255,255,0.06)'}`,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                      {item.status === 'unread' ? (
                        <Envelope size={18} weight="fill" style={{ color: '#e8524a', flexShrink: 0 }} />
                      ) : (
                        <EnvelopeOpen size={18} style={{ color: 'var(--jf-text-muted)', flexShrink: 0 }} />
                      )}
                      <Text style={{ color: '#e0e0e8', fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {item.service_name}
                      </Text>
                      {item.wechat_user_id && (
                        <Tooltip title={`微信用户 ID: ${item.wechat_user_id}${item.wechat_session_id ? `\n会话: ${item.wechat_session_id}` : ''}`}>
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
                      <Tag color={st.color} style={{ marginLeft: 4, fontSize: 11 }}>{st.label}</Tag>
                      {item.handled_by === 'agent' && (
                        <Tooltip title="已由 Agent 自动处理">
                          <Robot size={14} style={{ color: '#52c41a', flexShrink: 0 }} />
                        </Tooltip>
                      )}
                    </div>
                    <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12, flexShrink: 0, marginLeft: 12 }}>{time}</Text>
                  </div>

                  <Paragraph
                    style={{ color: '#c4c4d4', fontSize: 13, marginBottom: item.agent_response ? 8 : 4, whiteSpace: 'pre-wrap' }}
                    ellipsis={{ rows: 3, expandable: true, symbol: '展开' }}
                  >
                    {item.message}
                  </Paragraph>

                  {item.agent_response && (
                    <Collapse
                      size="small"
                      ghost
                      items={[{
                        key: '1',
                        label: <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>Agent 处理结果</Text>,
                        children: (
                          <Paragraph style={{ color: '#a0a0b8', fontSize: 12, margin: 0, whiteSpace: 'pre-wrap' }}>
                            {item.agent_response}
                          </Paragraph>
                        ),
                      }]}
                    />
                  )}

                  <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end', marginTop: 4 }}>
                    {item.status === 'unread' && (
                      <Tooltip title="标记已读">
                        <Button type="text" size="small" icon={<Eye size={14} />} onClick={() => handleMarkRead(item.id)} style={{ color: 'var(--jf-text-muted)' }} />
                      </Tooltip>
                    )}
                    <Popconfirm title="确定删除？" onConfirm={() => handleDelete(item.id)} okText="删除" cancelText="取消">
                      <Tooltip title="删除">
                        <Button type="text" size="small" danger icon={<Trash size={14} />} />
                      </Tooltip>
                    </Popconfirm>
                  </div>
                </div>
              );
            }}
          />
        )}
      </Spin>
    </div>
  );
}

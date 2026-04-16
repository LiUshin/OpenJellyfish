import { useState, useEffect, useRef, useCallback } from 'react';
import { Button, Spin, Tag, message } from 'antd';
import {
  WechatOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
  DisconnectOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import { fmtUserTime } from '../../utils/timezone';
import LogoLoading from '../../components/LogoLoading';
import * as api from '../../services/api';

interface SessionInfo {
  connected: boolean;
  connected_at?: string;
}

interface QrResult {
  qr_image_b64: string;
  qr_id: string;
}

interface QrStatus {
  status: 'waiting' | 'scanned' | 'confirmed' | 'expired';
}

interface WeChatMessage {
  role: string;
  content: string;
  timestamp?: string;
}

type PageState = 'loading' | 'disconnected' | 'qr' | 'connected';

const POLL_INTERVAL = 2500;

const S = {
  page: {
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    height: '100%',
    overflowY: 'auto' as const,
    padding: '40px 20px',
  },
  card: {
    background: 'var(--jf-bg-panel)',
    border: '1px solid var(--jf-border)',
    borderRadius: 'var(--jf-radius-lg)',
    padding: '40px 48px',
    maxWidth: 520,
    width: '100%',
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    gap: 20,
  },
  icon: (color: string) => ({
    fontSize: 56,
    color,
    marginBottom: 4,
  }),
  title: {
    color: 'var(--jf-text)',
    fontSize: 22,
    fontWeight: 600,
    margin: 0,
  },
  subtitle: {
    color: 'var(--jf-text-muted)',
    fontSize: 14,
    textAlign: 'center' as const,
    margin: 0,
    lineHeight: 1.6,
  },
  qrContainer: {
    background: '#fff',
    borderRadius: 'var(--jf-radius-md)',
    padding: 12,
    display: 'inline-block',
  },
  qrImg: {
    width: 256,
    height: 256,
    display: 'block',
  },
  statusLabel: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    color: 'var(--jf-text-muted)',
    fontSize: 13,
  },
  messagesCard: {
    background: 'var(--jf-bg-panel)',
    border: '1px solid var(--jf-border)',
    borderRadius: 'var(--jf-radius-lg)',
    maxWidth: 520,
    width: '100%',
    marginTop: 20,
    overflow: 'hidden',
  },
  messagesHeader: {
    padding: '14px 20px',
    borderBottom: '1px solid var(--jf-border)',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  messagesTitle: {
    color: 'var(--jf-text)',
    fontSize: 15,
    fontWeight: 500,
    margin: 0,
  },
  messagesList: {
    padding: '16px 20px',
    maxHeight: 400,
    overflowY: 'auto' as const,
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 10,
  },
  bubbleRow: (isUser: boolean) => ({
    display: 'flex',
    justifyContent: isUser ? 'flex-end' : 'flex-start',
  }),
  bubble: (isUser: boolean) => ({
    maxWidth: '80%',
    padding: '10px 14px',
    borderRadius: isUser
      ? 'var(--jf-radius-lg) var(--jf-radius-lg) var(--jf-radius-sm) var(--jf-radius-lg)'
      : 'var(--jf-radius-lg) var(--jf-radius-lg) var(--jf-radius-lg) var(--jf-radius-sm)',
    background: isUser ? 'var(--jf-legacy)' : 'var(--jf-bg-raised)',
    color: 'var(--jf-text)',
    fontSize: 13,
    lineHeight: 1.6,
    whiteSpace: 'pre-wrap' as const,
    wordBreak: 'break-word' as const,
  }),
  bubbleTime: {
    fontSize: 11,
    color: 'var(--jf-text-dim)',
    marginTop: 4,
  },
  emptyMsg: {
    color: 'var(--jf-text-dim)',
    textAlign: 'center' as const,
    padding: '32px 0',
    fontSize: 13,
  },
  connectedMeta: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    flexWrap: 'wrap' as const,
    justifyContent: 'center',
  },
} as const;

function formatTime(ts?: string): string {
  return fmtUserTime(ts, 'short');
}

export default function WeChatPage() {
  const [pageState, setPageState] = useState<PageState>('loading');
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [qr, setQr] = useState<QrResult | null>(null);
  const [qrStatus, setQrStatus] = useState<QrStatus['status']>('waiting');
  const [messages, setMessages] = useState<WeChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const autoQrRef = useRef(false);

  const checkSession = useCallback(async () => {
    try {
      const data = await api.request<SessionInfo>('GET', '/admin/wechat/session');
      setSession(data);
      if (data.connected) {
        setPageState('connected');
        stopPolling();
        const msgData = await api.request<{ messages: WeChatMessage[] }>(
          'GET',
          '/admin/wechat/messages',
        );
        setMessages(msgData.messages || []);
      } else {
        setPageState('disconnected');
      }
    } catch {
      setPageState('disconnected');
      setSession(null);
    }
  }, [stopPolling]);

  useEffect(() => {
    checkSession();
    return stopPolling;
  }, [checkSession, stopPolling]);

  useEffect(() => {
    if (pageState === 'disconnected' && !autoQrRef.current) {
      autoQrRef.current = true;
      handleGenerateQR();
    }
  }, [pageState]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleGenerateQR = async () => {
    setLoading(true);
    try {
      const data = await api.request<QrResult>('POST', '/admin/wechat/qrcode');
      setQr(data);
      setQrStatus('waiting');
      setPageState('qr');
      startPolling(data.qr_id);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '生成二维码失败');
    } finally {
      setLoading(false);
    }
  };

  const startPolling = (qrId: string) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const data = await api.request<QrStatus>(
          'GET',
          `/admin/wechat/qrcode/status?qrcode=${encodeURIComponent(qrId)}`,
        );
        setQrStatus(data.status);
        if (data.status === 'confirmed') {
          stopPolling();
          await checkSession();
        } else if (data.status === 'expired') {
          stopPolling();
        }
      } catch {
        stopPolling();
      }
    }, POLL_INTERVAL);
  };

  const handleDisconnect = async () => {
    setLoading(true);
    try {
      await api.request('DELETE', '/admin/wechat/session');
      message.success('已断开微信连接');
      setSession(null);
      setMessages([]);
      setPageState('disconnected');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '断开失败');
    } finally {
      setLoading(false);
    }
  };

  const handleRefreshMessages = async () => {
    try {
      const data = await api.request<{ messages: WeChatMessage[] }>(
        'GET',
        '/admin/wechat/messages',
      );
      setMessages(data.messages || []);
    } catch {
      message.error('刷新消息失败');
    }
  };

  if (pageState === 'loading') {
    return (
      <div style={{ ...S.page, justifyContent: 'center' }}>
        <LogoLoading size={240} />
      </div>
    );
  }

  return (
    <div style={S.page}>
      {/* Status Card */}
      <div style={S.card}>
        {pageState === 'disconnected' && (
          <>
            <CloseCircleOutlined style={S.icon('#e74c3c')} />
            <h2 style={S.title}>微信未连接</h2>
            <p style={S.subtitle}>
              扫描二维码登录微信，即可接收和处理微信消息。
            </p>
            <Button
              type="primary"
              size="large"
              icon={<WechatOutlined />}
              loading={loading}
              onClick={handleGenerateQR}
              style={{ marginTop: 8, background: '#07c160', borderColor: '#07c160' }}
            >
              生成微信二维码
            </Button>
          </>
        )}

        {pageState === 'qr' && qr && (
          <>
            <WechatOutlined style={S.icon('#07c160')} />
            <h2 style={S.title}>扫描二维码登录</h2>
            <div style={S.qrContainer}>
              <img
                src={`data:image/png;base64,${qr.qr_image_b64}`}
                alt="微信登录二维码"
                style={S.qrImg}
              />
            </div>
            <div style={S.statusLabel}>
              {qrStatus === 'waiting' && (
                <>
                  <LoadingOutlined />
                  <span>请用微信扫描上方二维码</span>
                </>
              )}
              {qrStatus === 'scanned' && (
                <>
                  <LoadingOutlined style={{ color: '#f39c12' }} />
                  <span style={{ color: '#f39c12' }}>已扫描，请在手机上确认登录</span>
                </>
              )}
              {qrStatus === 'expired' && (
                <>
                  <CloseCircleOutlined style={{ color: '#e74c3c' }} />
                  <span style={{ color: '#e74c3c' }}>二维码已过期</span>
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    onClick={handleGenerateQR}
                    loading={loading}
                  >
                    重新生成
                  </Button>
                </>
              )}
            </div>
          </>
        )}

        {pageState === 'connected' && (
          <>
            <CheckCircleOutlined style={S.icon('#00b894')} />
            <h2 style={S.title}>微信已连接</h2>
            <div style={S.connectedMeta}>
              <Tag color="green">在线</Tag>
              {session?.connected_at && (
                <span style={{ color: 'var(--jf-text-muted)', fontSize: 13 }}>
                  连接于 {formatTime(session.connected_at)}
                </span>
              )}
            </div>
            <Button
              danger
              icon={<DisconnectOutlined />}
              loading={loading}
              onClick={handleDisconnect}
            >
              断开连接
            </Button>
          </>
        )}
      </div>

      {/* Messages */}
      {pageState === 'connected' && (
        <div style={S.messagesCard}>
          <div style={S.messagesHeader}>
            <h3 style={S.messagesTitle}>对话记录</h3>
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              onClick={handleRefreshMessages}
              style={{ color: 'var(--jf-text-muted)' }}
            />
          </div>
          <div style={S.messagesList}>
            {messages.length === 0 ? (
              <div style={S.emptyMsg}>暂无对话记录</div>
            ) : (
              messages.map((msg, i) => {
                const isUser = msg.role === 'user';
                return (
                  <div key={i} style={S.bubbleRow(isUser)}>
                    <div>
                      <div style={S.bubble(isUser)}>{msg.content}</div>
                      {msg.timestamp && (
                        <div
                          style={{
                            ...S.bubbleTime,
                            textAlign: isUser ? 'right' : 'left',
                          }}
                        >
                          {formatTime(msg.timestamp)}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>
      )}
    </div>
  );
}

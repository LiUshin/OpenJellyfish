import { useState, type CSSProperties } from 'react';
import { Form, Input, Button, Tabs, Alert, Typography, Space } from 'antd';
import { UserOutlined, LockOutlined, KeyOutlined } from '@ant-design/icons';
import { useAuth } from '../stores/authContext';

const { Title, Text } = Typography;

export default function Login() {
  const { login, register } = useAuth();
  const [activeTab, setActiveTab] = useState('login');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleLogin(values: { username: string; password: string }) {
    setLoading(true);
    setError('');
    try {
      await login(values.username, values.password);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '登录失败');
    } finally {
      setLoading(false);
    }
  }

  async function handleRegister(values: { reg_key: string; username: string; password: string }) {
    setLoading(true);
    setError('');
    try {
      await register(values.username, values.password, values.reg_key);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '注册失败');
    } finally {
      setLoading(false);
    }
  }

  const submitBtnStyle: CSSProperties = {
    background: 'linear-gradient(135deg, var(--jf-gradient-from) 0%, var(--jf-gradient-to) 100%)',
    border: 'none',
    color: 'var(--jf-bg-deep)',
    fontWeight: 600,
    height: 44,
  };

  return (
    <>
      <style>{`
        @keyframes login-jellyfish-breathe {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.05); }
        }
        .login-brand-dots {
          position: absolute;
          inset: 0;
          pointer-events: none;
          opacity: 0.35;
          background-image: radial-gradient(rgba(var(--jf-primary-rgb), 0.12) 1px, transparent 1px);
          background-size: 20px 20px;
        }
        .login-jellyfish-logo {
          animation: login-jellyfish-breathe 3s ease-in-out infinite;
          max-width: min(280px, 70vw);
          height: auto;
          display: block;
        }
        .login-form-card .ant-tabs-nav::before {
          border-bottom-color: rgba(var(--jf-border-rgb), 0.5);
        }
        .login-form-card .ant-tabs-tab {
          color: var(--jf-text-muted) !important;
        }
        .login-form-card .ant-tabs-tab.ant-tabs-tab-active .ant-tabs-tab-btn {
          color: var(--jf-primary) !important;
        }
        .login-form-card .ant-tabs-ink-bar {
          background: var(--jf-primary) !important;
        }
        .login-form-card .ant-input-affix-wrapper,
        .login-form-card .ant-input-affix-wrapper input {
          background: var(--jf-bg-deep) !important;
          color: var(--jf-text) !important;
        }
        .login-form-card .ant-input-affix-wrapper {
          border-color: rgba(var(--jf-border-rgb), 0.6) !important;
        }
        .login-form-card .ant-input-affix-wrapper:hover,
        .login-form-card .ant-input-affix-wrapper:focus-within {
          border-color: var(--jf-primary) !important;
          box-shadow: 0 0 0 2px rgba(var(--jf-primary-rgb), 0.15) !important;
        }
        .login-form-card .anticon {
          color: var(--jf-text-muted) !important;
        }
        .login-submit-btn.ant-btn-primary:not(:disabled):hover {
          filter: brightness(1.1) !important;
        }
        .login-form-card .ant-alert-error {
          background: rgba(var(--jf-error-rgb), 0.12) !important;
          border-color: rgba(var(--jf-error-rgb), 0.35) !important;
        }
        .login-form-card .ant-alert-error .ant-alert-message {
          color: var(--jf-error) !important;
          font-weight: 500;
        }
        .login-form-card .ant-alert-error .ant-alert-icon {
          color: var(--jf-error) !important;
        }
        @media (max-width: 900px) {
          .login-split-root { flex-direction: column !important; }
          .login-brand-col { width: 100% !important; min-height: 38vh !important; }
          .login-form-col { width: 100% !important; flex: 1 !important; padding: 24px 16px 40px !important; }
        }
        /* Terminal style overrides */
        [data-style='terminal'] .login-form-card {
          border-radius: 0 !important;
          border: 1px solid var(--jf-border) !important;
          background: var(--jf-bg-deep) !important;
          box-shadow: 0 0 12px rgba(var(--jf-primary-rgb), 0.1) !important;
        }
        [data-style='terminal'] .login-form-card *,
        [data-style='terminal'] .login-brand-col * {
          font-family: 'JetBrains Mono', monospace !important;
        }
        [data-style='terminal'] .login-brand-dots {
          background-image: radial-gradient(rgba(var(--jf-primary-rgb), 0.08) 1px, transparent 1px) !important;
        }
        [data-style='terminal'] .login-form-card .ant-input-affix-wrapper {
          border-radius: 0 !important;
        }
        [data-style='terminal'] .login-submit-btn {
          border-radius: 0 !important;
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }
        [data-style='terminal'] .login-form-card .ant-alert {
          border-radius: 0 !important;
        }
        @keyframes terminal-cursor-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        [data-style='terminal'] .login-form-card .ant-input-affix-wrapper:focus-within::after {
          content: '█';
          position: absolute;
          right: 12px;
          color: var(--jf-primary);
          animation: terminal-cursor-blink 1s step-end infinite;
          font-size: 14px;
        }
      `}</style>
      <div
        className="login-split-root"
        style={{
          display: 'flex',
          minHeight: '100vh',
          width: '100%',
          background: 'var(--jf-bg-deep)',
        }}
      >
        <div
          className="login-brand-col"
          style={{
            position: 'relative',
            width: '40%',
            minWidth: 280,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '48px 32px',
            background: 'linear-gradient(180deg, var(--jf-bg-deep) 0%, var(--jf-bg-panel) 100%)',
            overflow: 'hidden',
          }}
        >
          <div className="login-brand-dots" aria-hidden />
          <Space
            direction="vertical"
            align="center"
            size={20}
            style={{ position: 'relative', zIndex: 1 }}
          >
            <img
              className="login-jellyfish-logo"
              src="/media_resources/jellyfishlogo.png"
              alt=""
            />
            <Title
              level={2}
              style={{
                margin: 0,
                color: 'var(--jf-text)',
                fontSize: 'clamp(1.75rem, 3vw, 2.25rem)',
                fontWeight: 700,
                letterSpacing: '-0.02em',
              }}
            >
              JellyfishBot
            </Title>
            <Text style={{ color: 'var(--jf-text-muted)', fontSize: 16 }}>
              Your Intelligent AI Companion 🪼
            </Text>
          </Space>
        </div>

        <div
          className="login-form-col"
          style={{
            width: '60%',
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '48px 40px',
            background: 'var(--jf-bg-raised)',
          }}
        >
          <div
            className="login-form-card"
            style={{
              width: '100%',
              maxWidth: 440,
              padding: '36px 32px',
              background: 'var(--jf-bg-panel)',
              border: '1px solid rgba(var(--jf-primary-rgb), 0.12)',
              borderRadius: 'var(--jf-radius-lg)',
              boxShadow: 'var(--jf-shadow-hover)',
            }}
          >
            <Tabs
              activeKey={activeTab}
              onChange={(key) => {
                setActiveTab(key);
                setError('');
              }}
              centered
              items={[
                {
                  key: 'login',
                  label: '登录',
                  children: (
                    <Form onFinish={handleLogin} layout="vertical" requiredMark={false}>
                      <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
                        <Input prefix={<UserOutlined />} placeholder="用户名" size="large" />
                      </Form.Item>
                      <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
                        <Input.Password prefix={<LockOutlined />} placeholder="密码" size="large" />
                      </Form.Item>
                      <Form.Item>
                        <Button
                          className="login-submit-btn"
                          type="primary"
                          htmlType="submit"
                          block
                          size="large"
                          loading={loading}
                          style={submitBtnStyle}
                        >
                          登录
                        </Button>
                      </Form.Item>
                    </Form>
                  ),
                },
                {
                  key: 'register',
                  label: '注册',
                  children: (
                    <Form onFinish={handleRegister} layout="vertical" requiredMark={false}>
                      <Form.Item name="reg_key" rules={[{ required: true, message: '请输入注册码' }]}>
                        <Input
                          prefix={<KeyOutlined />}
                          placeholder="注册码 (如 DA-XXXXXXXX-XXXXXXXX)"
                          size="large"
                        />
                      </Form.Item>
                      <Form.Item
                        name="username"
                        rules={[{ required: true, min: 2, message: '至少 2 个字符' }]}
                      >
                        <Input prefix={<UserOutlined />} placeholder="用户名" size="large" />
                      </Form.Item>
                      <Form.Item
                        name="password"
                        rules={[{ required: true, min: 4, message: '至少 4 个字符' }]}
                      >
                        <Input.Password prefix={<LockOutlined />} placeholder="密码" size="large" />
                      </Form.Item>
                      <Form.Item>
                        <Button
                          className="login-submit-btn"
                          type="primary"
                          htmlType="submit"
                          block
                          size="large"
                          loading={loading}
                          style={submitBtnStyle}
                        >
                          注册
                        </Button>
                      </Form.Item>
                    </Form>
                  ),
                },
              ]}
            />

            {error ? (
              <Alert
                message={error}
                type="error"
                showIcon
                style={{
                  marginTop: 12,
                  borderRadius: 'var(--jf-radius-md)',
                  background: 'rgba(var(--jf-error-rgb), 0.12)',
                  border: '1px solid rgba(var(--jf-error-rgb), 0.35)',
                  color: 'var(--jf-error)',
                }}
              />
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}

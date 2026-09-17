import { useState } from 'react';
import { Alert, Button, Form, Input, Space, Tag, Typography } from 'antd';
import RuntimeSettingsCard from '../../components/RuntimeSettingsCard';
import { hostClient, type HostClient } from '../../services/superadmin';

export default function SuperadminPage() {
  const [client, setClient] = useState<HostClient | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [form] = Form.useForm<{ key: string }>();
  async function login(values: { key: string }) {
    setBusy(true); setError('');
    const next = hostClient(values.key.trim(), () => { setClient(null); setError('超管 key 已失效，请重新登录。'); });
    try {
      await next.capabilities(); setClient(next); form.resetFields();
    } catch (e) { setError(e instanceof Error ? e.message : '登录失败'); }
    finally { setBusy(false); }
  }
  return <main style={{ minHeight: '100vh', background: 'var(--jf-bg-deep)', padding: '40px 20px' }}>
    <div style={{ maxWidth: 1080, margin: '0 auto' }}>
      <Space wrap style={{ width: '100%', justifyContent: 'space-between', marginBottom: 24 }}>
        <div><Typography.Title level={2} style={{ margin: 0 }}>OpenJellyfish 超管控制台</Typography.Title>
          <Typography.Text type="secondary">服务器与个人主机的管理入口</Typography.Text></div>
        <Space><Button href="/">打开 Jellyfish</Button>{client && <Button onClick={() => { setClient(null); setError(''); }}>退出超管</Button>}</Space>
      </Space>
      {error && <Alert showIcon type="error" message={error} style={{ marginBottom: 20 }} />}
      {!client ? <section style={{ maxWidth: 480, margin: '64px auto', padding: 28, background: 'var(--jf-bg-raised)', border: '1px solid var(--jf-border)', borderRadius: 16 }}>
        <Typography.Title level={3}>使用主机 key 访问</Typography.Title>
        <Typography.Paragraph type="secondary">此入口属于主机主人，独立于分发给团队成员的 admin 账号。</Typography.Paragraph>
        <Form form={form} layout="vertical" onFinish={login}>
          <Form.Item name="key" label="超管 key" rules={[{ required: true, message: '请输入主机 key' }]}>
            <Input.Password autoComplete="off" placeholder="jf_host_…" aria-label="超管 key" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy} block>进入超管控制台</Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ marginTop: 20 }}>在实际运行后端的环境中查看 key：</Typography.Paragraph>
        <Typography.Paragraph type="secondary">直接部署：<Typography.Text code>python3 launcher.py --superadmin-key</Typography.Text></Typography.Paragraph>
        <Typography.Paragraph type="secondary">Docker Compose：<Typography.Text code>docker compose exec openjellyfish python launcher.py --superadmin-key</Typography.Text></Typography.Paragraph>
        <Typography.Paragraph type="secondary">打包 App 可从控制台查看。刷新或关闭页面后需重新输入。</Typography.Paragraph>
      </section> : <>
        <Alert type="info" showIcon message="标准模式 · 可信团队共享" description="连接使用主机的 Codex / Cursor 套餐。只有获授权的 admin 可以使用指定模型；每个 admin 保留各自的聊天和文件目录。本机工作目录不提供 Docker 级隔离。" style={{ marginBottom: 20 }} />
        <Space style={{ marginBottom: 16 }}><Tag>主机身份</Tag><Typography.Text type="secondary">登录与授权操作在此集中管理</Typography.Text></Space>
        <RuntimeSettingsCard api={client} host />
      </>}
    </div>
  </main>;
}

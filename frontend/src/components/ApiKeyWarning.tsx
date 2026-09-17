import { useState, useEffect } from 'react';
import { Modal, Button, Typography } from 'antd';
import { WarningCircle } from '@phosphor-icons/react';
import { useNavigate } from 'react-router-dom';
import * as api from '../services/api';
import * as runtime from '../services/runtime';

const { Text } = Typography;

const DISMISSED_KEY = 'jf-api-key-warning-dismissed';

export default function ApiKeyWarning() {
  const [visible, setVisible] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const dismissed = sessionStorage.getItem(DISMISSED_KEY);
    if (dismissed) return;

    let disposed = false;
    api.getApiKeysStatus()
      .then(async status => {
        if (status.has_llm) return;
        const caps = await runtime.capabilities();
        if (caps.available) {
          const connections = await runtime.profiles();
          if (connections.some(p => p.status === 'ready' && !p.recovery_required && p.models.length)) return;
        }
        if (!disposed) setVisible(true);
      })
      .catch(() => {});
    return () => { disposed = true; };
  }, []);

  const handleGoSettings = () => {
    setVisible(false);
    sessionStorage.setItem(DISMISSED_KEY, '1');
    navigate('/settings/general');
  };

  const handleDismiss = () => {
    setVisible(false);
    sessionStorage.setItem(DISMISSED_KEY, '1');
  };

  return (
    <Modal
      open={visible}
      onCancel={handleDismiss}
      footer={null}
      centered
      width={440}
      closable
    >
      <div style={{ textAlign: 'center', padding: '16px 0' }}>
        <WarningCircle size={48} weight="fill" color="var(--jf-warning)" />
        <div style={{ marginTop: 16, marginBottom: 8 }}>
          <Text style={{ fontSize: 16, fontWeight: 600, color: 'var(--jf-text)' }}>
            尚未配置可用的聊天引擎
          </Text>
        </div>
        <Text style={{ color: 'var(--jf-text-muted)', fontSize: 13, display: 'block', marginBottom: 24 }}>
          可以配置 DeepAgents 的模型 API Key，或使用服务器开放的 Codex 或 Cursor 连接。
          生图、视频和语音需要单独配置相应能力。
        </Text>
        <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
          <Button onClick={handleDismiss}>稍后设置</Button>
          <Button type="primary" onClick={handleGoSettings}>
            前往设置
          </Button>
        </div>
      </div>
    </Modal>
  );
}

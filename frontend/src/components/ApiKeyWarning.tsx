import { useState, useEffect } from 'react';
import { Modal, Button, Typography } from 'antd';
import { WarningCircle } from '@phosphor-icons/react';
import { useNavigate } from 'react-router-dom';
import * as api from '../services/api';

const { Text } = Typography;

const DISMISSED_KEY = 'jf-api-key-warning-dismissed';

export default function ApiKeyWarning() {
  const [visible, setVisible] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const dismissed = sessionStorage.getItem(DISMISSED_KEY);
    if (dismissed) return;

    api.getApiKeysStatus()
      .then(status => {
        if (!status.has_llm) {
          setVisible(true);
        }
      })
      .catch(() => {});
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
            未配置 AI 模型 API Key
          </Text>
        </div>
        <Text style={{ color: 'var(--jf-text-muted)', fontSize: 13, display: 'block', marginBottom: 24 }}>
          需要至少配置 Claude 或 OpenAI 的 API Key 才能使用 Agent 对话功能。
          未配置 OpenAI Key 将无法使用图片生成、视频生成和语音功能。
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

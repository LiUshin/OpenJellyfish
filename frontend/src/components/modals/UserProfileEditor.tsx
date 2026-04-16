import { useState, useEffect, useCallback } from 'react';
import {
  Modal, Button, Space, Typography, Input, List, Tag, Popconfirm,
  Collapse, message, Tooltip, Spin,
} from 'antd';
import {
  SaveOutlined, HistoryOutlined, DeleteOutlined, EyeOutlined,
  RollbackOutlined, UserOutlined,
} from '@ant-design/icons';
import type { UserProfile, PromptVersion } from '../../types';
import * as api from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';

const { Text } = Typography;
const { TextArea } = Input;

interface Props {
  open: boolean;
  onClose: () => void;
  inline?: boolean;
}

const PLACEHOLDER = `在这里写下你希望 AI 始终遵守的个性化规则，例如：

- 用简洁专业的语气回复
- 重要结论先说，分析放后面
- 给出明确的操作建议
- 风险提示必须包含
- 回复中使用中文`;

const panelStyle: React.CSSProperties = {
  background: 'var(--jf-bg-panel)',
  border: '1px solid var(--jf-border)',
  borderRadius: 'var(--jf-radius-md)',
  padding: 12,
};

export default function UserProfileEditor({ open, onClose, inline }: Props) {
  const [rules, setRules] = useState('');
  const [originalRules, setOriginalRules] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(true);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewLabel, setPreviewLabel] = useState('');

  const loadProfile = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getUserProfile();
      const notes = res.profile?.custom_notes || '';
      setRules(notes);
      setOriginalRules(notes);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadVersions = useCallback(async () => {
    setVersionsLoading(true);
    try {
      const res = await api.listProfileVersions();
      setVersions(res);
    } catch { /* silent */ } finally {
      setVersionsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      loadProfile();
      loadVersions();
      setPreviewContent(null);
    }
  }, [open, loadProfile, loadVersions]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateUserProfile({ custom_notes: rules } as UserProfile);
      setOriginalRules(rules);
      message.success('个性规则已保存');
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handlePreview = async (id: string) => {
    try {
      const ver = await api.getProfileVersion(id);
      setPreviewContent(ver.content ?? '');
      setPreviewLabel(ver.label || ver.id);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '加载版本失败');
    }
  };

  const handleRollback = async (id: string) => {
    try {
      const res = await api.rollbackProfileVersion(id);
      setRules(res.content);
      setOriginalRules(res.content);
      message.success('已回滚');
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '回滚失败');
    }
  };

  const handleDeleteVersion = async (id: string) => {
    try {
      await api.deleteProfileVersion(id);
      message.success('已删除');
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  const hasChanges = rules !== originalRules;

  if (!open) return null;

  const content = (
    <>
      <Spin spinning={loading}>
        <div style={{ display: 'flex', gap: 16, position: 'relative' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ color: 'var(--jf-text-muted)', fontSize: 13, display: 'block', marginBottom: 12 }}>
              定义 AI 回复时始终遵守的个性化规则和偏好，这些规则会附加在每次对话的上下文中。
            </Text>
            <TextArea
              value={rules}
              onChange={(e) => setRules(e.target.value)}
              placeholder={PLACEHOLDER}
              autoSize={{ minRows: 14, maxRows: 26 }}
              style={{
                background: 'var(--jf-bg-deep)',
                border: '1px solid var(--jf-border)',
                color: 'var(--jf-text)',
                fontFamily: 'monospace',
                fontSize: 13,
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10 }}>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>
                {rules.length} 字符 {hasChanges && <Tag color="orange" style={{ marginLeft: 6 }}>未保存</Tag>}
              </Text>
              <Button
                type="primary"
                icon={<SaveOutlined />}
                size="small"
                loading={saving}
                disabled={!hasChanges}
                onClick={handleSave}
              >
                保存
              </Button>
            </div>
          </div>

          {showHistory && (
            <div style={{ width: 260, flexShrink: 0 }}>
              <Collapse
                defaultActiveKey={['history']}
                ghost
                items={[{
                  key: 'history',
                  label: (
                    <Space>
                      <HistoryOutlined />
                      <span style={{ color: 'var(--jf-text)' }}>版本历史</span>
                      <Tag>{versions.length}</Tag>
                    </Space>
                  ),
                  children: (
                    <Spin spinning={versionsLoading}>
                      <List
                        dataSource={versions}
                        size="small"
                        locale={{ emptyText: '暂无版本' }}
                        renderItem={(v) => (
                          <List.Item style={{ ...panelStyle, marginBottom: 6, padding: '8px 10px' }}>
                            <div style={{ width: '100%' }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <Text style={{ color: 'var(--jf-text)', fontSize: 12, fontWeight: 500 }} ellipsis>
                                  {v.label || '未命名'}
                                </Text>
                                <Tag style={{ fontSize: 10 }}>{v.char_count} 字</Tag>
                              </div>
                              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 11 }}>
                                {fmtUserTime(v.timestamp, 'datetime')}
                              </Text>
                              <div style={{ marginTop: 6, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                                <Tooltip title="预览">
                                  <Button size="small" type="text" icon={<EyeOutlined />} onClick={() => handlePreview(v.id)} />
                                </Tooltip>
                                <Tooltip title="回滚到此版本">
                                  <Popconfirm title="确定回滚？" onConfirm={() => handleRollback(v.id)} okText="确定" cancelText="取消">
                                    <Button size="small" type="text" icon={<RollbackOutlined />} />
                                  </Popconfirm>
                                </Tooltip>
                                <Tooltip title="删除">
                                  <Popconfirm title="确定删除此版本？" onConfirm={() => handleDeleteVersion(v.id)} okText="确定" cancelText="取消">
                                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                                  </Popconfirm>
                                </Tooltip>
                              </div>
                            </div>
                          </List.Item>
                        )}
                      />
                    </Spin>
                  ),
                }]}
              />
            </div>
          )}

          <Tooltip title={showHistory ? '收起版本' : '展开版本'}>
            <Button
              type="text"
              size="small"
              icon={<HistoryOutlined />}
              onClick={() => setShowHistory(!showHistory)}
              style={{ position: 'absolute', top: 0, right: 0, color: 'var(--jf-text-muted)' }}
            />
          </Tooltip>
        </div>
      </Spin>

      <Modal
        open={previewContent !== null}
        title={`预览: ${previewLabel}`}
        onCancel={() => setPreviewContent(null)}
        footer={
          <Button onClick={() => {
            if (previewContent !== null) {
              setRules(previewContent);
              setPreviewContent(null);
              message.info('已加载到编辑器，需手动保存');
            }
          }}>
            加载到编辑器
          </Button>
        }
        width={600}
        styles={{
          body: { padding: 16 },
          header: { background: 'var(--jf-bg-panel)', borderBottom: '1px solid var(--jf-border)' },
          content: { background: 'var(--jf-bg-panel)' },
        }}
      >
        <pre style={{
          background: 'var(--jf-bg-deep)',
          border: '1px solid var(--jf-border)',
          borderRadius: 'var(--jf-radius-md)',
          padding: 12,
          color: 'var(--jf-text)',
          fontSize: 12,
          maxHeight: 400,
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}>
          {previewContent}
        </pre>
      </Modal>
    </>
  );

  if (inline) {
    return <div style={{ padding: '16px 20px', height: '100%', overflow: 'auto' }}>{content}</div>;
  }

  return (
    <Modal
      open={open}
      onCancel={onClose}
      title={
        <Space>
          <UserOutlined style={{ color: 'var(--jf-legacy)' }} />
          <span>个性规则</span>
        </Space>
      }
      width={800}
      footer={null}
      destroyOnClose
      styles={{
        body: { padding: '16px 20px' },
        header: { background: 'var(--jf-bg-panel)', borderBottom: '1px solid var(--jf-border)' },
        content: { background: 'var(--jf-bg-panel)' },
      }}
    >
      {content}
    </Modal>
  );
}

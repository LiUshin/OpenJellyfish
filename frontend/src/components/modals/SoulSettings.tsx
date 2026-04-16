import { useState, useEffect, useCallback } from 'react';
import { Switch, Typography, Space, Spin, message, Divider, Tag, Input, Button, Popconfirm } from 'antd';
import { Brain, FolderOpen, Eye, PencilSimple, ArrowCounterClockwise, FloppyDisk } from '@phosphor-icons/react';
import * as api from '../../services/api';
import type { SoulConfig, CapabilityPromptItem } from '../../services/api';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

interface Props {
  open: boolean;
  onClose: () => void;
  inline?: boolean;
}

const CARD: React.CSSProperties = {
  background: 'var(--jf-bg-raised)',
  borderRadius: 'var(--jf-radius-lg)',
  padding: '20px 24px',
  marginBottom: 16,
};

const ICON_WRAP: React.CSSProperties = {
  width: 40,
  height: 40,
  borderRadius: 'var(--jf-radius-md)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexShrink: 0,
};

const SOUL_PROMPT_KEYS = ['memory_subagent', 'soul_edit'] as const;

const PROMPT_LABELS: Record<string, string> = {
  memory_subagent: 'Memory Subagent 提示词',
  soul_edit: 'Soul 文件系统 提示词',
};

export default function SoulSettings({ open, onClose, inline }: Props) {
  const [config, setConfig] = useState<SoulConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [updating, setUpdating] = useState<string | null>(null);
  const [prompts, setPrompts] = useState<Record<string, CapabilityPromptItem>>({});
  const [editTexts, setEditTexts] = useState<Record<string, string>>({});
  const [savingPrompt, setSavingPrompt] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [c, p] = await Promise.all([api.getSoulConfig(), api.getCapabilityPrompts()]);
      setConfig(c);
      const map: Record<string, CapabilityPromptItem> = {};
      for (const item of p.prompts) {
        if (SOUL_PROMPT_KEYS.includes(item.key as typeof SOUL_PROMPT_KEYS[number])) {
          map[item.key] = item;
        }
      }
      setPrompts(map);
      const texts: Record<string, string> = {};
      for (const key of SOUL_PROMPT_KEYS) {
        const item = map[key];
        if (item) texts[key] = item.custom ?? item.default;
      }
      setEditTexts(texts);
    } catch {
      message.error('加载 Soul 配置失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const toggle = async (key: keyof SoulConfig, value: boolean) => {
    if (!config) return;
    setUpdating(key);
    try {
      const res = await api.updateSoulConfig({ [key]: value });
      setConfig(res.config);
      message.success('设置已更新');
    } catch {
      message.error('更新失败');
    } finally {
      setUpdating(null);
    }
  };

  const savePrompt = async (key: string) => {
    setSavingPrompt(key);
    try {
      await api.updateCapabilityPrompt(key, editTexts[key] || '');
      message.success('提示词已保存');
      const item = prompts[key];
      if (item) {
        setPrompts({ ...prompts, [key]: { ...item, custom: editTexts[key] || null } });
      }
    } catch {
      message.error('保存失败');
    } finally {
      setSavingPrompt(null);
    }
  };

  const resetPrompt = async (key: string) => {
    setSavingPrompt(key);
    try {
      await api.resetCapabilityPrompt(key);
      const item = prompts[key];
      if (item) {
        setEditTexts({ ...editTexts, [key]: item.default });
        setPrompts({ ...prompts, [key]: { ...item, custom: null } });
      }
      message.success('已恢复默认');
    } catch {
      message.error('重置失败');
    } finally {
      setSavingPrompt(null);
    }
  };

  const isPromptModified = (key: string) => {
    const item = prompts[key];
    if (!item) return false;
    const current = editTexts[key] ?? '';
    const original = item.custom ?? item.default;
    return current !== original;
  };

  if (!open) return null;
  if (loading || !config) {
    return (
      <div style={{ padding: 40, textAlign: 'center' }}>
        <Spin />
      </div>
    );
  }

  const renderPromptEditor = (key: string, enabled: boolean) => {
    const item = prompts[key];
    if (!item || !enabled) return null;
    const isCustom = item.custom !== null;
    return (
      <div style={{
        marginTop: 12, padding: '12px 16px', borderRadius: 'var(--jf-radius-md)',
        background: 'var(--jf-bg-panel)', border: '1px solid var(--jf-border)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <Space size={6}>
            <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>{PROMPT_LABELS[key]}</Text>
            {isCustom && <Tag color="orange" style={{ fontSize: 10, lineHeight: '14px', padding: '0 4px' }}>已自定义</Tag>}
          </Space>
          <Space size={4}>
            {isCustom && (
              <Popconfirm title="恢复为默认提示词？" onConfirm={() => resetPrompt(key)} okText="确定" cancelText="取消">
                <Button
                  size="small" type="text"
                  icon={<ArrowCounterClockwise size={14} />}
                  style={{ color: 'var(--jf-text-muted)' }}
                  loading={savingPrompt === key}
                />
              </Popconfirm>
            )}
            <Button
              size="small" type="text"
              icon={<FloppyDisk size={14} />}
              style={{ color: isPromptModified(key) ? 'var(--jf-primary)' : 'var(--jf-text-muted)' }}
              disabled={!isPromptModified(key)}
              loading={savingPrompt === key}
              onClick={() => savePrompt(key)}
            />
          </Space>
        </div>
        <TextArea
          value={editTexts[key] ?? ''}
          onChange={(e) => setEditTexts({ ...editTexts, [key]: e.target.value })}
          autoSize={{ minRows: 4, maxRows: 12 }}
          style={{
            background: 'var(--jf-bg-deep)', border: '1px solid var(--jf-border)',
            color: 'var(--jf-text)', fontFamily: 'monospace', fontSize: 12,
          }}
        />
      </div>
    );
  };

  const content = (
    <Space direction="vertical" size={0} style={{ width: '100%' }}>
      <div style={{ marginBottom: 20 }}>
        <Space align="center" size={10}>
          <Brain size={22} weight="duotone" color="var(--jf-primary)" />
          <Text strong style={{ fontSize: 16, color: 'var(--jf-text)' }}>
            Memory & Soul
          </Text>
          <Tag color="purple" style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
            Advanced
          </Tag>
        </Space>
        <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 13, marginTop: 6, marginBottom: 0 }}>
          控制 Agent 的长期记忆和自我认知能力。Memory Subagent 可以管理笔记，Soul Edit 可以让文件面板直接管理灵魂文件。
        </Paragraph>
      </div>

      <div style={CARD}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
          <div style={{ ...ICON_WRAP, background: 'rgba(var(--jf-primary-rgb), 0.12)' }}>
            <PencilSimple size={22} weight="duotone" color="var(--jf-primary)" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <Text strong style={{ fontSize: 14, color: 'var(--jf-text)' }}>Memory Subagent 写入</Text>
              <Switch
                checked={config.memory_subagent_enabled}
                loading={updating === 'memory_subagent_enabled'}
                onChange={(v) => toggle('memory_subagent_enabled', v)}
                style={config.memory_subagent_enabled ? { backgroundColor: 'var(--jf-primary)' } : undefined}
              />
            </div>
            <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 12, marginBottom: 0, lineHeight: '18px' }}>
              启用后，Memory Subagent 可以在 <code style={{ color: 'var(--jf-accent)', fontSize: 11 }}>soul/</code> 目录下创建、编辑、删除笔记和文件。
              对话记录本身保持只读，Subagent 不能修改已发生的聊天历史。
            </Paragraph>
            {config.memory_subagent_enabled && (
              <div style={{
                marginTop: 10, padding: '8px 12px', borderRadius: 'var(--jf-radius-sm)',
                background: 'rgba(var(--jf-primary-rgb), 0.06)',
                border: '1px solid rgba(var(--jf-primary-rgb), 0.15)',
              }}>
                <Text style={{ color: 'var(--jf-text-muted)', fontSize: 11 }}>
                  <Eye size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
                  对话历史：只读
                  <span style={{ margin: '0 8px', color: 'var(--jf-text-dim)' }}>|</span>
                  <PencilSimple size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
                  soul/ 文件：读写
                </Text>
              </div>
            )}
            {renderPromptEditor('memory_subagent', config.memory_subagent_enabled)}
          </div>
        </div>
      </div>

      <div style={CARD}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
          <div style={{ ...ICON_WRAP, background: 'rgba(var(--jf-secondary-rgb), 0.12)' }}>
            <FolderOpen size={22} weight="duotone" color="var(--jf-secondary)" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <Text strong style={{ fontSize: 14, color: 'var(--jf-text)' }}>Soul 文件系统</Text>
              <Switch
                checked={config.soul_edit_enabled}
                loading={updating === 'soul_edit_enabled'}
                onChange={(v) => toggle('soul_edit_enabled', v)}
                style={config.soul_edit_enabled ? { backgroundColor: 'var(--jf-primary)' } : undefined}
              />
            </div>
            <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 12, marginBottom: 0, lineHeight: '18px' }}>
              启用后，<code style={{ color: 'var(--jf-accent)', fontSize: 11 }}>soul/</code> 目录会出现在文件面板中，你可以直接浏览和管理全部灵魂文件。
              同时 Agent 也可以在对话中直接读写 soul/ 下的文件。
            </Paragraph>
            {config.soul_edit_enabled && (
              <div style={{
                marginTop: 10, padding: '8px 12px', borderRadius: 'var(--jf-radius-sm)',
                background: 'rgba(var(--jf-secondary-rgb), 0.06)',
                border: '1px solid rgba(var(--jf-secondary-rgb), 0.15)',
              }}>
                <Text style={{ color: 'var(--jf-text-muted)', fontSize: 11 }}>
                  <FolderOpen size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
                  文件面板中可见 soul/ 目录
                  <span style={{ margin: '0 8px', color: 'var(--jf-text-dim)' }}>|</span>
                  <PencilSimple size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
                  Agent 可直接修改 soul/ 内文件
                </Text>
              </div>
            )}
            {renderPromptEditor('soul_edit', config.soul_edit_enabled)}
          </div>
        </div>
      </div>

      <Divider style={{ margin: '12px 0', borderColor: 'var(--jf-border)' }} />

      <div style={CARD}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
          <div style={{ ...ICON_WRAP, background: 'rgba(var(--jf-accent-rgb), 0.12)' }}>
            <Brain size={22} weight="duotone" color="var(--jf-accent)" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <Text strong style={{ fontSize: 14, color: 'var(--jf-text)' }}>包含消费者对话</Text>
              <Switch
                checked={config.include_consumer_conversations}
                loading={updating === 'include_consumer_conversations'}
                onChange={(v) => toggle('include_consumer_conversations', v)}
                style={config.include_consumer_conversations ? { backgroundColor: 'var(--jf-primary)' } : undefined}
              />
            </div>
            <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 12, marginBottom: 0, lineHeight: '18px' }}>
              允许 Memory Subagent 读取 Service 消费者的对话记录。关闭时，记忆范围仅限管理员自己的对话。
            </Paragraph>
          </div>
        </div>
      </div>
    </Space>
  );

  if (inline) {
    return (
      <div style={{ padding: '16px 20px', height: '100%', overflow: 'auto' }}>
        {content}
      </div>
    );
  }

  return null;
}

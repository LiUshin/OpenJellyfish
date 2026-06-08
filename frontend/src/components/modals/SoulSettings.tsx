import { useState, useEffect, useCallback } from 'react';
import { Switch, Typography, Space, Spin, message, Divider, Tag, Input, Button, Popconfirm } from 'antd';
import { Brain, FolderOpen, Eye, PencilSimple, ArrowCounterClockwise, FloppyDisk } from '@phosphor-icons/react';
import { useTranslation, Trans } from 'react-i18next';
import * as api from '../../services/api';
import type { SoulConfig, CapabilityPromptItem } from '../../services/api';
import { useIsMobile } from '../../hooks/useMediaQuery';

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

const CARD_MOBILE: React.CSSProperties = {
  ...CARD,
  padding: '16px 14px',
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

const PROMPT_LABEL_KEYS: Record<string, string> = {
  memory_subagent: 'soul.promptLabelMemory',
  soul_edit: 'soul.promptLabelSoul',
};

export default function SoulSettings({ open, onClose, inline }: Props) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
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
      message.error(t('soul.loadFail'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const toggle = async (key: keyof SoulConfig, value: boolean) => {
    if (!config) return;
    setUpdating(key);
    try {
      const res = await api.updateSoulConfig({ [key]: value });
      setConfig(res.config);
      message.success(t('soul.updateSuccess'));
    } catch {
      message.error(t('soul.updateFail'));
    } finally {
      setUpdating(null);
    }
  };

  const savePrompt = async (key: string) => {
    setSavingPrompt(key);
    try {
      await api.updateCapabilityPrompt(key, editTexts[key] || '');
      message.success(t('soul.promptSaved'));
      const item = prompts[key];
      if (item) {
        setPrompts({ ...prompts, [key]: { ...item, custom: editTexts[key] || null } });
      }
    } catch {
      message.error(t('soul.promptSaveFail'));
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
      message.success(t('soul.promptResetSuccess'));
    } catch {
      message.error(t('soul.promptResetFail'));
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
            <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>{t(PROMPT_LABEL_KEYS[key])}</Text>
            {isCustom && <Tag color="orange" style={{ fontSize: 10, lineHeight: '14px', padding: '0 4px' }}>{t('soul.customizedTag')}</Tag>}
          </Space>
          <Space size={4}>
            {isCustom && (
              <Popconfirm title={t('soul.promptResetConfirm')} onConfirm={() => resetPrompt(key)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
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
            {t('soul.title')}
          </Text>
          <Tag color="purple" style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
            {t('soul.advTag')}
          </Tag>
        </Space>
        <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 13, marginTop: 6, marginBottom: 0 }}>
          {t('soul.intro')}
        </Paragraph>
      </div>

      <div style={isMobile ? CARD_MOBILE : CARD}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: isMobile ? 10 : 16 }}>
          <div style={{ ...ICON_WRAP, background: 'rgba(var(--jf-primary-rgb), 0.12)' }}>
            <PencilSimple size={22} weight="duotone" color="var(--jf-primary)" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <Text strong style={{ fontSize: 14, color: 'var(--jf-text)' }}>{t('soul.memorySubagentTitle')}</Text>
              <Switch
                checked={config.memory_subagent_enabled}
                loading={updating === 'memory_subagent_enabled'}
                onChange={(v) => toggle('memory_subagent_enabled', v)}
                style={config.memory_subagent_enabled ? { backgroundColor: 'var(--jf-primary)' } : undefined}
              />
            </div>
            <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 12, marginBottom: 0, lineHeight: '18px' }}>
              <Trans i18nKey="soul.memorySubagentDesc">
                When on, the Memory Subagent can create / edit / delete notes and files under <code style={{ color: 'var(--jf-accent)', fontSize: 11 }}>soul/</code>. Conversation history stays read-only — the subagent can never modify past chats.
              </Trans>
            </Paragraph>
            {config.memory_subagent_enabled && (
              <div style={{
                marginTop: 10, padding: '8px 12px', borderRadius: 'var(--jf-radius-sm)',
                background: 'rgba(var(--jf-primary-rgb), 0.06)',
                border: '1px solid rgba(var(--jf-primary-rgb), 0.15)',
              }}>
                <Text style={{ color: 'var(--jf-text-muted)', fontSize: 11 }}>
                  <Eye size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
                  {t('soul.convReadOnly')}
                  <span style={{ margin: '0 8px', color: 'var(--jf-text-dim)' }}>|</span>
                  <PencilSimple size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
                  {t('soul.soulRW')}
                </Text>
              </div>
            )}
            {renderPromptEditor('memory_subagent', config.memory_subagent_enabled)}
          </div>
        </div>
      </div>

      <div style={isMobile ? CARD_MOBILE : CARD}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: isMobile ? 10 : 16 }}>
          <div style={{ ...ICON_WRAP, background: 'rgba(var(--jf-secondary-rgb), 0.12)' }}>
            <FolderOpen size={22} weight="duotone" color="var(--jf-secondary)" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <Text strong style={{ fontSize: 14, color: 'var(--jf-text)' }}>{t('soul.soulFsTitle')}</Text>
              <Switch
                checked={config.soul_edit_enabled}
                loading={updating === 'soul_edit_enabled'}
                onChange={(v) => toggle('soul_edit_enabled', v)}
                style={config.soul_edit_enabled ? { backgroundColor: 'var(--jf-primary)' } : undefined}
              />
            </div>
            <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 12, marginBottom: 0, lineHeight: '18px' }}>
              <Trans i18nKey="soul.soulFsDesc">
                When on, the <code style={{ color: 'var(--jf-accent)', fontSize: 11 }}>soul/</code> folder appears in the file panel — browse and manage all soul files directly. The agent can also read/write files under soul/ in chat.
              </Trans>
            </Paragraph>
            {config.soul_edit_enabled && (
              <div style={{
                marginTop: 10, padding: '8px 12px', borderRadius: 'var(--jf-radius-sm)',
                background: 'rgba(var(--jf-secondary-rgb), 0.06)',
                border: '1px solid rgba(var(--jf-secondary-rgb), 0.15)',
              }}>
                <Text style={{ color: 'var(--jf-text-muted)', fontSize: 11 }}>
                  <FolderOpen size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
                  {t('soul.soulFsVisible')}
                  <span style={{ margin: '0 8px', color: 'var(--jf-text-dim)' }}>|</span>
                  <PencilSimple size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
                  {t('soul.soulFsAgentEdit')}
                </Text>
              </div>
            )}
            {renderPromptEditor('soul_edit', config.soul_edit_enabled)}
          </div>
        </div>
      </div>

      <Divider style={{ margin: '12px 0', borderColor: 'var(--jf-border)' }} />

      <div style={isMobile ? CARD_MOBILE : CARD}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: isMobile ? 10 : 16 }}>
          <div style={{ ...ICON_WRAP, background: 'rgba(var(--jf-accent-rgb), 0.12)' }}>
            <Brain size={22} weight="duotone" color="var(--jf-accent)" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <Text strong style={{ fontSize: 14, color: 'var(--jf-text)' }}>{t('soul.consumerConvTitle')}</Text>
              <Switch
                checked={config.include_consumer_conversations}
                loading={updating === 'include_consumer_conversations'}
                onChange={(v) => toggle('include_consumer_conversations', v)}
                style={config.include_consumer_conversations ? { backgroundColor: 'var(--jf-primary)' } : undefined}
              />
            </div>
            <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 12, marginBottom: 0, lineHeight: '18px' }}>
              {t('soul.consumerConvDesc')}
            </Paragraph>
          </div>
        </div>
      </div>
    </Space>
  );

  if (inline) {
    return (
      <div style={{
        padding: isMobile ? '12px 12px 24px' : '16px 20px',
        height: '100%',
        overflow: 'auto',
      }}>
        {content}
      </div>
    );
  }

  return null;
}

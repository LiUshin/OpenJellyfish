import { useState, useEffect, useCallback } from 'react';
import {
  Modal, Button, Space, Typography, Input, List, Tag, Popconfirm,
  Collapse, message, Tooltip, Spin, Switch,
} from 'antd';
import {
  SaveOutlined, HistoryOutlined, DeleteOutlined, EyeOutlined,
  RollbackOutlined, UserOutlined, EditOutlined, RobotOutlined,
  LockOutlined, UnlockOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { UserProfile, PromptVersion } from '../../types';
import * as api from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';
import { useIsMobile } from '../../hooks/useMediaQuery';

const { Text } = Typography;
const { TextArea } = Input;

interface Props {
  open: boolean;
  onClose: () => void;
  inline?: boolean;
}

const panelStyle: React.CSSProperties = {
  background: 'var(--jf-bg-panel)',
  border: '1px solid var(--jf-border)',
  borderRadius: 'var(--jf-radius-md)',
  padding: 12,
};

export default function UserProfileEditor({ open, onClose, inline }: Props) {
  const isMobile = useIsMobile();
  const { t } = useTranslation();

  // ── 个性规则（用户手写） ────────────────────────────
  const [rules, setRules] = useState('');
  const [originalRules, setOriginalRules] = useState('');
  const [saving, setSaving] = useState(false);

  // ── Agent 记忆（agent 通过 update_personal_memory 写） ──
  const [agentNotes, setAgentNotes] = useState('');
  const [originalAgentNotes, setOriginalAgentNotes] = useState('');
  const [agentLocked, setAgentLocked] = useState(false);
  const [originalAgentLocked, setOriginalAgentLocked] = useState(false);
  const [savingAgent, setSavingAgent] = useState(false);

  // ── 共享 state ────────────────────────────────────
  const [loading, setLoading] = useState(false);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(true);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewLabel, setPreviewLabel] = useState('');
  const [editingMeta, setEditingMeta] = useState<{ id: string; label: string; note: string } | null>(null);

  const loadProfile = useCallback(async () => {
    setLoading(true);
    try {
      const [profileRes, agentRes] = await Promise.all([
        api.getUserProfile(),
        api.getAgentNotes(),
      ]);
      const notes = profileRes.profile?.custom_notes || '';
      setRules(notes);
      setOriginalRules(notes);
      setAgentNotes(agentRes.content || '');
      setOriginalAgentNotes(agentRes.content || '');
      setAgentLocked(!!agentRes.locked);
      setOriginalAgentLocked(!!agentRes.locked);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('common.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

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
      message.success(t('profile.rulesSaved'));
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('common.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAgent = async () => {
    setSavingAgent(true);
    try {
      await api.updateAgentNotes(agentNotes, agentLocked);
      setOriginalAgentNotes(agentNotes);
      setOriginalAgentLocked(agentLocked);
      message.success(t('profile.agentMemorySaved'));
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('common.saveFailed'));
    } finally {
      setSavingAgent(false);
    }
  };

  const handleClearAgent = () => {
    setAgentNotes('');
  };

  const handlePreview = async (id: string) => {
    try {
      const ver = await api.getProfileVersion(id);
      setPreviewContent(ver.content ?? '');
      setPreviewLabel(ver.label || ver.id);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.loadVersionFailed'));
    }
  };

  const handleRollback = async (id: string) => {
    try {
      const res = await api.rollbackProfileVersion(id);
      setRules(res.content);
      setOriginalRules(res.content);
      message.success(t('profile.rolledBack'));
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.rollbackFailed'));
    }
  };

  const handleDeleteVersion = async (id: string) => {
    try {
      await api.deleteProfileVersion(id);
      message.success(t('profile.deleted'));
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.deleteFailed'));
    }
  };

  const handleSaveMeta = async () => {
    if (!editingMeta) return;
    try {
      await api.updateProfileVersionMeta(editingMeta.id, editingMeta.label, editingMeta.note);
      message.success(t('profile.renameSaved'));
      setEditingMeta(null);
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.metaUpdateFailed'));
    }
  };

  const hasChanges = rules !== originalRules;
  const hasAgentChanges = agentNotes !== originalAgentNotes || agentLocked !== originalAgentLocked;

  if (!open) return null;

  // ── Agent 记忆区（顶部独立面板） ───────────────────
  const agentSection = (
    <div
      style={{
        marginBottom: 16,
        padding: 12,
        background: 'rgba(95, 201, 230, 0.06)',
        border: '1px solid rgba(95, 201, 230, 0.25)',
        borderRadius: 'var(--jf-radius-md)',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 8,
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        <Space size={6}>
          <RobotOutlined style={{ color: 'var(--jf-accent, #5FC9E6)' }} />
          <Text style={{ color: 'var(--jf-text)', fontSize: 13, fontWeight: 600 }}>
            {t('profile.agentMemory')}
          </Text>
          <Tooltip
            title={agentLocked ? t('profile.agentMemoryLockedTip') : t('profile.agentMemoryUnlockedTip')}
          >
            <Tag
              color={agentLocked ? 'red' : 'cyan'}
              style={{ fontSize: 10, marginLeft: 0 }}
            >
              {agentLocked ? t('profile.agentMemoryLocked') : t('profile.agentMemoryWritable')}
            </Tag>
          </Tooltip>
        </Space>
        <Space size={6}>
          <Tooltip title={agentLocked ? t('profile.agentMemoryDoUnlock') : t('profile.agentMemoryDoLock')}>
            <Switch
              size="small"
              checked={agentLocked}
              checkedChildren={<LockOutlined />}
              unCheckedChildren={<UnlockOutlined />}
              onChange={setAgentLocked}
            />
          </Tooltip>
        </Space>
      </div>
      <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12, display: 'block', marginBottom: 8 }}>
        {t('profile.agentMemoryDesc')}
      </Text>
      <TextArea
        value={agentNotes}
        onChange={(e) => setAgentNotes(e.target.value)}
        placeholder={t('profile.agentMemoryPlaceholder')}
        autoSize={{ minRows: 5, maxRows: 14 }}
        style={{
          background: 'var(--jf-bg-deep)',
          border: '1px solid var(--jf-border)',
          color: 'var(--jf-text)',
          fontFamily: 'monospace',
          fontSize: 12,
        }}
      />
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginTop: 8,
          flexWrap: 'wrap',
          gap: 6,
        }}
      >
        <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>
          {t('profile.charsCount', { count: agentNotes.length })}
          {hasAgentChanges && (
            <Tag color="orange" style={{ marginLeft: 6 }}>
              {t('common.unsaved')}
            </Tag>
          )}
        </Text>
        <Space size={6}>
          <Popconfirm
            title={t('profile.agentMemoryClearConfirm')}
            description={t('profile.agentMemoryClearDesc')}
            onConfirm={handleClearAgent}
            okText={t('common.clear')}
            cancelText={t('common.cancel')}
          >
            <Button
              size="small"
              type="text"
              icon={<DeleteOutlined />}
              disabled={!agentNotes}
              style={{ color: 'var(--jf-text-muted)' }}
            >
              {t('common.clear')}
            </Button>
          </Popconfirm>
          <Button
            type="primary"
            ghost
            icon={<SaveOutlined />}
            size="small"
            loading={savingAgent}
            disabled={!hasAgentChanges}
            onClick={handleSaveAgent}
          >
            {t('common.save')}
          </Button>
        </Space>
      </div>
    </div>
  );

  const content = (
    <>
      <Spin spinning={loading}>
        {agentSection}

        <div style={{
          display: 'flex',
          gap: isMobile ? 12 : 16,
          position: 'relative',
          flexDirection: isMobile ? 'column' : 'row',
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Space size={6} style={{ marginBottom: 8 }}>
              <UserOutlined style={{ color: 'var(--jf-legacy)' }} />
              <Text style={{ color: 'var(--jf-text)', fontSize: 13, fontWeight: 600 }}>
                {t('profile.rules')}
              </Text>
            </Space>
            <Text style={{ color: 'var(--jf-text-muted)', fontSize: 13, display: 'block', marginBottom: 12 }}>
              {t('profile.rulesDesc')}
            </Text>
            <TextArea
              value={rules}
              onChange={(e) => setRules(e.target.value)}
              placeholder={t('profile.rulesPlaceholder')}
              autoSize={{ minRows: 12, maxRows: 24 }}
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
                {t('profile.charsCount', { count: rules.length })} {hasChanges && <Tag color="orange" style={{ marginLeft: 6 }}>{t('common.unsaved')}</Tag>}
              </Text>
              <Button
                type="primary"
                icon={<SaveOutlined />}
                size="small"
                loading={saving}
                disabled={!hasChanges}
                onClick={handleSave}
              >
                {t('common.save')}
              </Button>
            </div>
          </div>

          {showHistory && (
            <div style={{ width: isMobile ? '100%' : 260, flexShrink: 0 }}>
              <Collapse
                defaultActiveKey={['history']}
                ghost
                items={[{
                  key: 'history',
                  label: (
                    <Space>
                      <HistoryOutlined />
                      <span style={{ color: 'var(--jf-text)' }}>{t('common.history')}</span>
                      <Tag>{versions.length}</Tag>
                    </Space>
                  ),
                  children: (
                    <Spin spinning={versionsLoading}>
                      <List
                        dataSource={versions}
                        size="small"
                        locale={{ emptyText: t('profile.emptyHistory') }}
                        renderItem={(v) => (
                          <List.Item style={{ ...panelStyle, marginBottom: 6, padding: '8px 10px' }}>
                            <div style={{ width: '100%' }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <Text style={{ color: 'var(--jf-text)', fontSize: 12, fontWeight: 500 }} ellipsis>
                                  {v.label || t('profile.emptyHistory')}
                                </Text>
                                <Tag style={{ fontSize: 10 }}>{t('profile.charsCount', { count: v.char_count })}</Tag>
                              </div>
                              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 11 }}>
                                {fmtUserTime(v.timestamp, 'datetime')}
                              </Text>
                              <div style={{ marginTop: 6, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                                <Tooltip title={t('common.preview')}>
                                  <Button size="small" type="text" icon={<EyeOutlined />} onClick={() => handlePreview(v.id)} />
                                </Tooltip>
                                <Tooltip title={t('profile.rollbackTip')}>
                                  <Popconfirm title={t('profile.rollbackConfirm')} onConfirm={() => handleRollback(v.id)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
                                    <Button size="small" type="text" icon={<RollbackOutlined />} />
                                  </Popconfirm>
                                </Tooltip>
                                <Tooltip title={t('common.rename')}>
                                  <Button
                                    size="small"
                                    type="text"
                                    icon={<EditOutlined />}
                                    onClick={() => setEditingMeta({ id: v.id, label: v.label, note: v.note })}
                                  />
                                </Tooltip>
                                <Tooltip title={t('common.delete')}>
                                  <Popconfirm title={t('profile.deleteVersionConfirm')} onConfirm={() => handleDeleteVersion(v.id)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
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

          <Tooltip title={showHistory ? t('profile.collapseHistory') : t('profile.expandHistory')}>
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
        title={t('profile.previewTitle', { label: previewLabel })}
        onCancel={() => setPreviewContent(null)}
        footer={
          <Button onClick={() => {
            if (previewContent !== null) {
              setRules(previewContent);
              setPreviewContent(null);
              message.info(t('profile.loadedToEditor'));
            }
          }}>
            {t('profile.loadIntoEditor')}
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

      <Modal
        open={editingMeta !== null}
        title={t('profile.renameTitle')}
        onCancel={() => setEditingMeta(null)}
        onOk={handleSaveMeta}
        okText={t('common.save')}
        cancelText={t('common.cancel')}
        styles={{
          header: { background: 'var(--jf-bg-panel)', borderBottom: '1px solid var(--jf-border)' },
          content: { background: 'var(--jf-bg-panel)' },
        }}
      >
        {editingMeta && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <div>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>{t('profile.renameNameLabel')}</Text>
              <Input
                value={editingMeta.label}
                onChange={(e) => setEditingMeta({ ...editingMeta, label: e.target.value })}
                style={{ background: 'var(--jf-bg-deep)', border: '1px solid var(--jf-border)', color: 'var(--jf-text)' }}
              />
            </div>
            <div>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>{t('profile.renameNoteLabel')}</Text>
              <TextArea
                value={editingMeta.note}
                onChange={(e) => setEditingMeta({ ...editingMeta, note: e.target.value })}
                rows={3}
                style={{ background: 'var(--jf-bg-deep)', border: '1px solid var(--jf-border)', color: 'var(--jf-text)' }}
              />
            </div>
          </Space>
        )}
      </Modal>
    </>
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

  return (
    <Modal
      open={open}
      onCancel={onClose}
      title={
        <Space>
          <UserOutlined style={{ color: 'var(--jf-legacy)' }} />
          <span>{t('profile.modalTitle')}</span>
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

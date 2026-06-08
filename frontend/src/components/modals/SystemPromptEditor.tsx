import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Modal, Input, Button, Space, Typography, List, Tag, Popconfirm,
  Collapse, message, Tooltip, Spin,
} from 'antd';
import {
  SaveOutlined, UndoOutlined, HistoryOutlined,
  DeleteOutlined, EyeOutlined, EditOutlined,
  SwapOutlined, RollbackOutlined,
} from '@ant-design/icons';
import { Lightning, FloppyDisk, ArrowCounterClockwise } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import type { PromptVersion } from '../../types';
import * as api from '../../services/api';
import type { CapabilityPromptItem } from '../../services/api';
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

const SOUL_KEYS = new Set(['memory_subagent', 'soul_edit']);
// Capability label keys; raw key falls back when no translation exists.
const CAP_LABEL_KEYS: Record<string, string> = {
  web: 'capabilities.web',
  image: 'capabilities.image',
  speech: 'capabilities.speech',
  video: 'capabilities.video',
  scheduler: 'capabilities.scheduler',
  service_scheduler: 'capabilities.service_scheduler',
  service_broadcast: 'capabilities.service_broadcast',
  contact_admin: 'capabilities.contact_admin',
  humanchat: 'capabilities.humanchat',
};

export default function SystemPromptEditor({ open, onClose, inline }: Props) {
  const isMobile = useIsMobile();
  const { t } = useTranslation();
  const [prompt, setPrompt] = useState('');
  const [originalPrompt, setOriginalPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewLabel, setPreviewLabel] = useState('');
  const [editingMeta, setEditingMeta] = useState<{ id: string; label: string; note: string } | null>(null);
  const [diffPair, setDiffPair] = useState<[string | null, string | null]>([null, null]);
  const [diffTexts, setDiffTexts] = useState<[string, string] | null>(null);
  const [showHistory, setShowHistory] = useState(true);

  const [capPrompts, setCapPrompts] = useState<CapabilityPromptItem[]>([]);
  const [capEdits, setCapEdits] = useState<Record<string, string>>({});
  const [capSaving, setCapSaving] = useState<string | null>(null);
  const [capExpanded, setCapExpanded] = useState<string | null>(null);

  const loadPrompt = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getSystemPrompt();
      setPrompt(res.prompt);
      setOriginalPrompt(res.prompt);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('common.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  const loadVersions = useCallback(async () => {
    setVersionsLoading(true);
    try {
      const res = await api.listPromptVersions();
      setVersions(res);
    } catch { /* silent */ } finally {
      setVersionsLoading(false);
    }
  }, []);

  const loadCapPrompts = useCallback(async () => {
    try {
      const res = await api.getCapabilityPrompts();
      const filtered = res.prompts.filter(p => !SOUL_KEYS.has(p.key));
      setCapPrompts(filtered);
      const edits: Record<string, string> = {};
      for (const p of filtered) edits[p.key] = p.custom ?? p.default;
      setCapEdits(edits);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    if (open) {
      loadPrompt();
      loadVersions();
      loadCapPrompts();
      setPreviewContent(null);
      setDiffTexts(null);
      setDiffPair([null, null]);
    }
  }, [open, loadPrompt, loadVersions, loadCapPrompts]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateSystemPrompt(prompt);
      setOriginalPrompt(prompt);
      message.success(t('prompt.saveSuccess'));
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    try {
      const res = await api.resetSystemPrompt();
      setPrompt(res.prompt);
      setOriginalPrompt(res.prompt);
      message.success(t('prompt.resetSuccess'));
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('common.saveFailed'));
    }
  };

  const handlePreview = async (id: string) => {
    try {
      const ver = await api.getPromptVersion(id);
      setPreviewContent(ver.content ?? '');
      setPreviewLabel(ver.label || ver.id);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.loadVersionFailed'));
    }
  };

  const handleRollback = async (id: string) => {
    try {
      const res = await api.rollbackPromptVersion(id);
      setPrompt(res.prompt);
      setOriginalPrompt(res.prompt);
      message.success(t('prompt.rolledBack'));
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.rollbackFailed'));
    }
  };

  const handleDeleteVersion = async (id: string) => {
    try {
      await api.deletePromptVersion(id);
      message.success(t('prompt.deleted'));
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.deleteFailed'));
    }
  };

  const handleSaveMeta = async () => {
    if (!editingMeta) return;
    try {
      await api.updatePromptVersionMeta(editingMeta.id, editingMeta.label, editingMeta.note);
      message.success(t('prompt.renameSaved'));
      setEditingMeta(null);
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.metaUpdateFailed'));
    }
  };

  const toggleDiff = (id: string) => {
    setDiffPair(([a, b]) => {
      if (a === id) return [null, b];
      if (b === id) return [a, null];
      if (!a) return [id, b];
      return [a, id];
    });
  };

  const handleDiffCompare = async () => {
    const [idA, idB] = diffPair;
    if (!idA || !idB) return;
    try {
      const [verA, verB] = await Promise.all([
        api.getPromptVersion(idA),
        api.getPromptVersion(idB),
      ]);
      setDiffTexts([verA.content ?? '', verB.content ?? '']);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('prompt.loadVersionFailed'));
    }
  };

  const diffLines = useMemo(() => {
    if (!diffTexts) return null;
    const [textA, textB] = diffTexts;
    const linesA = textA.split('\n');
    const linesB = textB.split('\n');
    const maxLen = Math.max(linesA.length, linesB.length);
    const result: { lineA: string; lineB: string; changed: boolean }[] = [];
    for (let i = 0; i < maxLen; i++) {
      const a = linesA[i] ?? '';
      const b = linesB[i] ?? '';
      result.push({ lineA: a, lineB: b, changed: a !== b });
    }
    return result;
  }, [diffTexts]);

  const saveCapPrompt = async (key: string) => {
    setCapSaving(key);
    try {
      await api.updateCapabilityPrompt(key, capEdits[key] || '');
      message.success(t('prompt.capPromptSaveSuccess'));
      setCapPrompts(prev => prev.map(p => p.key === key ? { ...p, custom: capEdits[key] || null } : p));
    } catch {
      message.error(t('prompt.capPromptSaveFailed'));
    } finally {
      setCapSaving(null);
    }
  };

  const resetCapPrompt = async (key: string) => {
    setCapSaving(key);
    try {
      await api.resetCapabilityPrompt(key);
      const item = capPrompts.find(p => p.key === key);
      if (item) {
        setCapEdits(prev => ({ ...prev, [key]: item.default }));
        setCapPrompts(prev => prev.map(p => p.key === key ? { ...p, custom: null } : p));
      }
      message.success(t('prompt.capPromptResetSuccess'));
    } catch {
      message.error(t('prompt.capPromptResetFailed'));
    } finally {
      setCapSaving(null);
    }
  };

  const hasChanges = prompt !== originalPrompt;

  if (!open) return null;

  const content = (
    <>
      <Spin spinning={loading}>
        <div style={{
          display: 'flex',
          gap: isMobile ? 12 : 16,
          position: 'relative',
          flexDirection: isMobile ? 'column' : 'row',
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <TextArea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              autoSize={{ minRows: 16, maxRows: 28 }}
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
                {t('profile.charsCount', { count: prompt.length })} {hasChanges && <Tag color="orange" style={{ marginLeft: 6 }}>{t('common.unsaved')}</Tag>}
              </Text>
              <Space>
                <Popconfirm title={t('prompt.resetConfirm')} onConfirm={handleReset} okText={t('common.confirm')} cancelText={t('common.cancel')}>
                  <Button icon={<UndoOutlined />} size="small">{t('prompt.resetDefault')}</Button>
                </Popconfirm>
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
              </Space>
            </div>
          </div>

          {showHistory && (
            <div style={{ width: isMobile ? '100%' : 280, flexShrink: 0 }}>
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
                      {(diffPair[0] || diffPair[1]) && (
                        <div style={{ marginBottom: 8 }}>
                          <Button
                            size="small"
                            icon={<SwapOutlined />}
                            disabled={!diffPair[0] || !diffPair[1]}
                            onClick={handleDiffCompare}
                          >
                            {t('prompt.diffCompare')}
                          </Button>
                        </div>
                      )}
                      <List
                        dataSource={versions}
                        size="small"
                        locale={{ emptyText: t('profile.emptyHistory') }}
                        renderItem={(v) => (
                          <List.Item
                            style={{
                              ...panelStyle,
                              marginBottom: 6,
                              padding: '8px 10px',
                              borderColor: diffPair.includes(v.id) ? 'var(--jf-legacy)' : 'var(--jf-border)',
                            }}
                          >
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
                                  <Popconfirm title={t('prompt.rollbackConfirm')} onConfirm={() => handleRollback(v.id)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
                                    <Button size="small" type="text" icon={<RollbackOutlined />} />
                                  </Popconfirm>
                                </Tooltip>
                                <Tooltip title={t('common.edit')}>
                                  <Button
                                    size="small"
                                    type="text"
                                    icon={<EditOutlined />}
                                    onClick={() => setEditingMeta({ id: v.id, label: v.label, note: v.note })}
                                  />
                                </Tooltip>
                                <Tooltip title={t('prompt.selectForCompare')}>
                                  <Button
                                    size="small"
                                    type={diffPair.includes(v.id) ? 'primary' : 'text'}
                                    icon={<SwapOutlined />}
                                    onClick={() => toggleDiff(v.id)}
                                  />
                                </Tooltip>
                                <Tooltip title={t('common.delete')}>
                                  <Popconfirm title={t('prompt.deleteVersionConfirm')} onConfirm={() => handleDeleteVersion(v.id)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
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

      {capPrompts.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Collapse
            ghost
            items={[{
              key: 'cap-prompts',
              label: (
                <Space>
                  <Lightning size={16} weight="duotone" color="var(--jf-secondary)" />
                  <span style={{ color: 'var(--jf-text)' }}>{t('prompt.capPromptsTitle')}</span>
                  <Tag>{capPrompts.length}</Tag>
                  {capPrompts.some(p => p.custom !== null) && (
                    <Tag color="orange" style={{ fontSize: 10 }}>
                      {t('prompt.capPromptCustomizedCount', { count: capPrompts.filter(p => p.custom !== null).length })}
                    </Tag>
                  )}
                </Space>
              ),
              children: (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {capPrompts.map(item => {
                    const expanded = capExpanded === item.key;
                    const isCustom = item.custom !== null;
                    const isModified = (capEdits[item.key] ?? '') !== (item.custom ?? item.default);
                    const labelKey = CAP_LABEL_KEYS[item.key];
                    const localisedLabel = labelKey ? t(labelKey) : item.key;
                    return (
                      <div
                        key={item.key}
                        style={{
                          background: 'var(--jf-bg-panel)', border: '1px solid var(--jf-border)',
                          borderRadius: 'var(--jf-radius-md)', padding: '8px 12px',
                        }}
                      >
                        <div
                          style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
                          onClick={() => setCapExpanded(expanded ? null : item.key)}
                        >
                          <Space size={6}>
                            <Text style={{ color: 'var(--jf-text)', fontSize: 13 }}>
                              {localisedLabel}
                            </Text>
                            {isCustom && (
                              <Tag color="orange" style={{ fontSize: 10, lineHeight: '14px', padding: '0 4px' }}>
                                {t('prompt.capPromptCustomized')}
                              </Tag>
                            )}
                          </Space>
                          <Text style={{ color: 'var(--jf-text-dim)', fontSize: 11 }}>
                            {expanded ? t('prompt.collapse') : t('prompt.expand')}
                          </Text>
                        </div>
                        {expanded && (
                          <div style={{ marginTop: 8 }}>
                            <TextArea
                              value={capEdits[item.key] ?? ''}
                              onChange={(e) => setCapEdits(prev => ({ ...prev, [item.key]: e.target.value }))}
                              autoSize={{ minRows: 3, maxRows: 14 }}
                              style={{
                                background: 'var(--jf-bg-deep)', border: '1px solid var(--jf-border)',
                                color: 'var(--jf-text)', fontFamily: 'monospace', fontSize: 12,
                              }}
                            />
                            <div style={{ marginTop: 6, display: 'flex', justifyContent: 'flex-end', gap: 4 }}>
                              {isCustom && (
                                <Popconfirm title={t('prompt.capPromptResetConfirm')} onConfirm={() => resetCapPrompt(item.key)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
                                  <Button
                                    size="small" type="text"
                                    icon={<ArrowCounterClockwise size={14} />}
                                    loading={capSaving === item.key}
                                    style={{ color: 'var(--jf-text-muted)' }}
                                  >
                                    {t('prompt.resetDefault')}
                                  </Button>
                                </Popconfirm>
                              )}
                              <Button
                                size="small" type="text"
                                icon={<FloppyDisk size={14} />}
                                loading={capSaving === item.key}
                                disabled={!isModified}
                                onClick={() => saveCapPrompt(item.key)}
                                style={{ color: isModified ? 'var(--jf-primary)' : 'var(--jf-text-muted)' }}
                              >
                                {t('common.save')}
                              </Button>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ),
            }]}
          />
        </div>
      )}

      <Modal
        open={previewContent !== null}
        title={t('prompt.previewTitle', { label: previewLabel })}
        onCancel={() => setPreviewContent(null)}
        footer={
          <Button onClick={() => {
            if (previewContent !== null) {
              setPrompt(previewContent);
              setPreviewContent(null);
              message.info(t('prompt.loadedToEditor'));
            }
          }}>
            {t('prompt.loadIntoEditor')}
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
        title={t('prompt.renameTitle')}
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
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>{t('prompt.renameNameLabel')}</Text>
              <Input
                value={editingMeta.label}
                onChange={(e) => setEditingMeta({ ...editingMeta, label: e.target.value })}
                style={{ background: 'var(--jf-bg-deep)', border: '1px solid var(--jf-border)', color: 'var(--jf-text)' }}
              />
            </div>
            <div>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>{t('prompt.renameNoteLabel')}</Text>
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

      <Modal
        open={diffLines !== null}
        title={t('prompt.diffTitle')}
        onCancel={() => { setDiffTexts(null); setDiffPair([null, null]); }}
        footer={null}
        width={800}
        styles={{
          body: { padding: 16 },
          header: { background: 'var(--jf-bg-panel)', borderBottom: '1px solid var(--jf-border)' },
          content: { background: 'var(--jf-bg-panel)' },
        }}
      >
        <div style={{ maxHeight: 500, overflow: 'auto' }}>
          {diffLines?.map((line, idx) => (
            <div
              key={idx}
              style={{
                display: 'flex',
                gap: 8,
                fontSize: 12,
                fontFamily: 'monospace',
                lineHeight: '20px',
                background: line.changed ? 'rgba(108,92,231,0.1)' : 'transparent',
                borderLeft: line.changed ? '3px solid var(--jf-legacy)' : '3px solid transparent',
                paddingLeft: 6,
              }}
            >
              <Text style={{ color: 'var(--jf-text-dim)', width: 24, flexShrink: 0, textAlign: 'right' }}>{idx + 1}</Text>
              <div style={{ flex: 1, color: line.changed ? '#e74c3c' : 'var(--jf-text-muted)', minWidth: 0 }}>
                <Text style={{ color: 'inherit', fontSize: 12 }} ellipsis>{line.lineA || ' '}</Text>
              </div>
              <div style={{ flex: 1, color: line.changed ? '#00b894' : 'var(--jf-text-muted)', minWidth: 0 }}>
                <Text style={{ color: 'inherit', fontSize: 12 }} ellipsis>{line.lineB || ' '}</Text>
              </div>
            </div>
          ))}
        </div>
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
          <EditOutlined style={{ color: 'var(--jf-legacy)' }} />
          <span>{t('prompt.modalTitle')}</span>
        </Space>
      }
      width={900}
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

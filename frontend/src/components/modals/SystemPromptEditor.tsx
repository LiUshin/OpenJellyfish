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
import type { PromptVersion } from '../../types';
import * as api from '../../services/api';
import type { CapabilityPromptItem } from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';

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
const CAP_LABELS: Record<string, string> = {
  web: '联网工具',
  image: 'AI 图片生成',
  speech: 'AI 语音生成',
  video: 'AI 视频生成',
  scheduler: '定时任务',
  service_scheduler: 'Service 定时任务',
  service_broadcast: 'Service 广播',
  contact_admin: '联系管理员',
  humanchat: 'HumanChat 模式',
};

export default function SystemPromptEditor({ open, onClose, inline }: Props) {
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
      message.error(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

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
      message.success('已保存');
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    try {
      const res = await api.resetSystemPrompt();
      setPrompt(res.prompt);
      setOriginalPrompt(res.prompt);
      message.success('已恢复默认');
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '操作失败');
    }
  };

  const handlePreview = async (id: string) => {
    try {
      const ver = await api.getPromptVersion(id);
      setPreviewContent(ver.content ?? '');
      setPreviewLabel(ver.label || ver.id);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '加载版本失败');
    }
  };

  const handleRollback = async (id: string) => {
    try {
      const res = await api.rollbackPromptVersion(id);
      setPrompt(res.prompt);
      setOriginalPrompt(res.prompt);
      message.success('已回滚');
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '回滚失败');
    }
  };

  const handleDeleteVersion = async (id: string) => {
    try {
      await api.deletePromptVersion(id);
      message.success('已删除');
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  const handleSaveMeta = async () => {
    if (!editingMeta) return;
    try {
      await api.updatePromptVersionMeta(editingMeta.id, editingMeta.label, editingMeta.note);
      message.success('备注已更新');
      setEditingMeta(null);
      loadVersions();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '更新失败');
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
      message.error(e instanceof Error ? e.message : '获取版本失败');
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
      message.success('提示词已保存');
      setCapPrompts(prev => prev.map(p => p.key === key ? { ...p, custom: capEdits[key] || null } : p));
    } catch {
      message.error('保存失败');
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
      message.success('已恢复默认');
    } catch {
      message.error('重置失败');
    } finally {
      setCapSaving(null);
    }
  };

  const hasChanges = prompt !== originalPrompt;

  if (!open) return null;

  const content = (
    <>
      <Spin spinning={loading}>
        <div style={{ display: 'flex', gap: 16, position: 'relative' }}>
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
                {prompt.length} 字符 {hasChanges && <Tag color="orange" style={{ marginLeft: 6 }}>未保存</Tag>}
              </Text>
              <Space>
                <Popconfirm title="确定恢复默认 Prompt？" onConfirm={handleReset} okText="确定" cancelText="取消">
                  <Button icon={<UndoOutlined />} size="small">恢复默认</Button>
                </Popconfirm>
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
              </Space>
            </div>
          </div>

          {showHistory && (
            <div style={{ width: 280, flexShrink: 0 }}>
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
                      {(diffPair[0] || diffPair[1]) && (
                        <div style={{ marginBottom: 8 }}>
                          <Button
                            size="small"
                            icon={<SwapOutlined />}
                            disabled={!diffPair[0] || !diffPair[1]}
                            onClick={handleDiffCompare}
                          >
                            对比选中版本
                          </Button>
                        </div>
                      )}
                      <List
                        dataSource={versions}
                        size="small"
                        locale={{ emptyText: '暂无版本' }}
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
                                <Tooltip title="编辑备注">
                                  <Button
                                    size="small"
                                    type="text"
                                    icon={<EditOutlined />}
                                    onClick={() => setEditingMeta({ id: v.id, label: v.label, note: v.note })}
                                  />
                                </Tooltip>
                                <Tooltip title="选择对比">
                                  <Button
                                    size="small"
                                    type={diffPair.includes(v.id) ? 'primary' : 'text'}
                                    icon={<SwapOutlined />}
                                    onClick={() => toggleDiff(v.id)}
                                  />
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

      {capPrompts.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Collapse
            ghost
            items={[{
              key: 'cap-prompts',
              label: (
                <Space>
                  <Lightning size={16} weight="duotone" color="var(--jf-secondary)" />
                  <span style={{ color: 'var(--jf-text)' }}>能力提示词</span>
                  <Tag>{capPrompts.length}</Tag>
                  {capPrompts.some(p => p.custom !== null) && (
                    <Tag color="orange" style={{ fontSize: 10 }}>
                      {capPrompts.filter(p => p.custom !== null).length} 已自定义
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
                              {CAP_LABELS[item.key] || item.key}
                            </Text>
                            {isCustom && (
                              <Tag color="orange" style={{ fontSize: 10, lineHeight: '14px', padding: '0 4px' }}>
                                已自定义
                              </Tag>
                            )}
                          </Space>
                          <Text style={{ color: 'var(--jf-text-dim)', fontSize: 11 }}>
                            {expanded ? '收起' : '展开'}
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
                                <Popconfirm title="恢复默认？" onConfirm={() => resetCapPrompt(item.key)} okText="确定" cancelText="取消">
                                  <Button
                                    size="small" type="text"
                                    icon={<ArrowCounterClockwise size={14} />}
                                    loading={capSaving === item.key}
                                    style={{ color: 'var(--jf-text-muted)' }}
                                  >
                                    恢复默认
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
                                保存
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
        title={`预览: ${previewLabel}`}
        onCancel={() => setPreviewContent(null)}
        footer={
          <Button onClick={() => {
            if (previewContent !== null) {
              setPrompt(previewContent);
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

      <Modal
        open={editingMeta !== null}
        title="编辑版本备注"
        onCancel={() => setEditingMeta(null)}
        onOk={handleSaveMeta}
        okText="保存"
        cancelText="取消"
        styles={{
          header: { background: 'var(--jf-bg-panel)', borderBottom: '1px solid var(--jf-border)' },
          content: { background: 'var(--jf-bg-panel)' },
        }}
      >
        {editingMeta && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <div>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>标签</Text>
              <Input
                value={editingMeta.label}
                onChange={(e) => setEditingMeta({ ...editingMeta, label: e.target.value })}
                style={{ background: 'var(--jf-bg-deep)', border: '1px solid var(--jf-border)', color: 'var(--jf-text)' }}
              />
            </div>
            <div>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>备注</Text>
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
        title="版本对比"
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
    return <div style={{ padding: '16px 20px', height: '100%', overflow: 'auto' }}>{content}</div>;
  }

  return (
    <Modal
      open={open}
      onCancel={onClose}
      title={
        <Space>
          <EditOutlined style={{ color: 'var(--jf-legacy)' }} />
          <span>System Prompt 编辑器</span>
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

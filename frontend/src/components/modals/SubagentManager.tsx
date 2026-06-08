import { useState, useEffect, useCallback } from 'react';
import {
  Modal, Button, Space, Typography, List, Tag, Switch,
  Form, Input, Select, Checkbox, Popconfirm, message, Spin, Empty,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined,
  RobotOutlined, ToolOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { SubagentConfig } from '../../types';
import * as api from '../../services/api';
import { useIsMobile } from '../../hooks/useMediaQuery';

const { Text } = Typography;
const { TextArea } = Input;

interface Props {
  open: boolean;
  onClose: () => void;
  inline?: boolean;
}

export default function SubagentManager({ open, onClose, inline }: Props) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [subagents, setSubagents] = useState<SubagentConfig[]>([]);
  const [availableTools, setAvailableTools] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editing, setEditing] = useState<SubagentConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const loadSubagents = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listSubagents();
      setSubagents(res.subagents);
      setAvailableTools(res.available_tools);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('subagentMgr.loadFail'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (open) loadSubagents();
  }, [open, loadSubagents]);

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await api.updateSubagent(id, { enabled });
      setSubagents((prev) => prev.map((s) => s.id === id ? { ...s, enabled } : s));
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('subagentMgr.opFail'));
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteSubagent(id);
      message.success(t('subagentMgr.deleteSuccess'));
      loadSubagents();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('subagentMgr.deleteFail'));
    }
  };

  const openEdit = (agent?: SubagentConfig) => {
    setEditing(agent ?? null);
    if (agent) {
      form.setFieldsValue({
        name: agent.name,
        description: agent.description,
        system_prompt: agent.system_prompt,
        tools: agent.tools,
        model: agent.model || undefined,
      });
    } else {
      form.resetFields();
    }
    setEditModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const config = {
        name: values.name,
        description: values.description || '',
        system_prompt: values.system_prompt || '',
        tools: values.tools || [],
        model: values.model || undefined,
      };

      if (editing) {
        await api.updateSubagent(editing.id, config);
        message.success(t('subagentMgr.updated'));
      } else {
        await api.addSubagent(config);
        message.success(t('subagentMgr.created'));
      }
      setEditModalOpen(false);
      loadSubagents();
    } catch (e: unknown) {
      if (e instanceof Error) message.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  const inputStyle: React.CSSProperties = {
    background: 'var(--jf-bg-deep)',
    border: '1px solid var(--jf-border)',
    color: 'var(--jf-text)',
  };

  const content = (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Text style={{ color: 'var(--jf-text-muted)', fontSize: 13 }}>
          {t('subagentMgr.summary', { total: subagents.length, enabled: subagents.filter((s) => s.enabled).length })}
        </Text>
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => openEdit()}>
          {t('subagentMgr.newBtn')}
        </Button>
      </div>

      <Spin spinning={loading}>
        <List
          dataSource={subagents}
          locale={{ emptyText: <Empty description={t('subagentMgr.empty')} /> }}
          renderItem={(agent) => (
            <div
              style={{
                background: 'var(--jf-bg-deep)',
                border: '1px solid var(--jf-border)',
                borderRadius: 'var(--jf-radius-md)',
                padding: '12px 16px',
                marginBottom: 8,
                opacity: agent.enabled ? 1 : 0.6,
              }}
            >
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'flex-start',
                flexWrap: isMobile ? 'wrap' : 'nowrap',
                gap: isMobile ? 8 : 0,
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <Text style={{ color: 'var(--jf-text)', fontWeight: 600, fontSize: 14 }}>
                      {agent.name}
                    </Text>
                    {agent.builtin && <Tag color="purple" style={{ fontSize: 10 }}>{t('subagentMgr.builtin')}</Tag>}
                    {agent.model && <Tag style={{ fontSize: 10 }}>{agent.model}</Tag>}
                  </div>
                  <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>{agent.description}</Text>
                  <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {agent.tools.map((tool) => (
                      <Tag key={tool} color="geekblue" style={{ fontSize: 10 }}>
                        <ToolOutlined /> {tool}
                      </Tag>
                    ))}
                  </div>
                </div>
                <Space style={{ flexShrink: 0, marginLeft: isMobile ? 0 : 12 }}>
                  <Switch
                    checked={agent.enabled}
                    size="small"
                    onChange={(checked) => handleToggle(agent.id, checked)}
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => openEdit(agent)}
                    style={{ color: 'var(--jf-text-muted)' }}
                  />
                  {!agent.builtin && (
                    <Popconfirm
                      title={t('subagentMgr.deleteConfirm')}
                      onConfirm={() => handleDelete(agent.id)}
                      okText={t('common.confirm')}
                      cancelText={t('common.cancel')}
                    >
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  )}
                </Space>
              </div>
            </div>
          )}
        />
      </Spin>

      {/* Edit/Create Sub-Modal */}
      <Modal
        open={editModalOpen}
        title={editing ? t('subagentMgr.modalEdit', { name: editing.name }) : t('subagentMgr.modalNew')}
        onCancel={() => setEditModalOpen(false)}
        onOk={handleSave}
        okText={editing ? t('common.update') : t('common.create')}
        cancelText={t('common.cancel')}
        confirmLoading={saving}
        width={isMobile ? '100%' : 600}
        style={isMobile ? { top: 0, paddingBottom: 0, maxWidth: '100%', margin: 0 } : undefined}
        styles={{
          body: { padding: '16px 20px' },
          header: { background: 'var(--jf-bg-panel)', borderBottom: '1px solid var(--jf-border)' },
          content: { background: 'var(--jf-bg-panel)' },
        }}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            label={<Text style={{ color: 'var(--jf-text-muted)' }}>{t('subagentMgr.fieldName')}</Text>}
            name="name"
            rules={[{ required: true, message: t('subagentMgr.fieldNameRequired') }]}
          >
            <Input style={inputStyle} placeholder={t('subagentMgr.fieldNamePlaceholder')} />
          </Form.Item>
          <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>{t('subagentMgr.fieldDesc')}</Text>} name="description">
            <Input style={inputStyle} placeholder={t('subagentMgr.fieldDescPlaceholder')} />
          </Form.Item>
          <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>{t('subagentMgr.fieldSystemPrompt')}</Text>} name="system_prompt">
            <TextArea
              rows={4}
              style={{ ...inputStyle, fontFamily: 'monospace', fontSize: 12 }}
              placeholder={t('subagentMgr.fieldSystemPromptPh')}
            />
          </Form.Item>
          <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>{t('subagentMgr.fieldModel')}</Text>} name="model">
            <Select
              allowClear
              placeholder={t('subagentMgr.fieldModelDefault')}
              options={[
                { label: 'gpt-4o', value: 'gpt-4o' },
                { label: 'gpt-4o-mini', value: 'gpt-4o-mini' },
                { label: 'claude-3.5-sonnet', value: 'claude-3-5-sonnet-20241022' },
                { label: 'deepseek-chat', value: 'deepseek-chat' },
              ]}
            />
          </Form.Item>
          <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>{t('subagentMgr.fieldTools')}</Text>} name="tools">
            <Checkbox.Group style={{ width: '100%' }}>
              <div style={{
                display: 'grid',
                gridTemplateColumns: isMobile ? 'repeat(2, 1fr)' : 'repeat(3, 1fr)',
                gap: '6px 12px',
                maxHeight: 200,
                overflow: 'auto',
                padding: '8px 4px',
              }}>
                {availableTools.map((tool) => (
                  <Checkbox key={tool} value={tool} style={{ color: 'var(--jf-text)', fontSize: 12 }}>
                    {tool}
                  </Checkbox>
                ))}
              </div>
            </Checkbox.Group>
          </Form.Item>
        </Form>
      </Modal>
    </>
  );

  if (inline) {
    return (
      <div style={{
        padding: isMobile ? '16px 12px 24px' : '16px 20px',
        paddingLeft: isMobile ? 52 : undefined,
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
          <RobotOutlined style={{ color: 'var(--jf-legacy)' }} />
          <span>{t('subagentMgr.title')}</span>
        </Space>
      }
      width={720}
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

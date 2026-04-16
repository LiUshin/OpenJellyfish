import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Modal, Button, Space, Typography, Upload, Form, Input, Select,
  Table, Progress, Tag, message, Steps, Spin, InputNumber,
} from 'antd';
import {
  UploadOutlined, PlayCircleOutlined, StopOutlined,
  DownloadOutlined, InboxOutlined, ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { PromptVersion, BatchSheetInfo, BatchResult, BatchTask } from '../../types';
import * as api from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';

const { Text, Title } = Typography;
const { Dragger } = Upload;

interface Props {
  open: boolean;
  onClose: () => void;
  inline?: boolean;
}

type Step = 0 | 1 | 2;

export default function BatchRunner({ open, onClose, inline }: Props) {
  const [step, setStep] = useState<Step>(0);
  const [uploading, setUploading] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<string | null>(null);
  const [sheets, setSheets] = useState<BatchSheetInfo[]>([]);
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [promptVersions, setPromptVersions] = useState<PromptVersion[]>([]);
  const [historyTasks, setHistoryTasks] = useState<BatchTask[]>([]);

  const [form] = Form.useForm();
  const [taskId, setTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<BatchTask | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const loadModelsAndVersions = useCallback(async () => {
    try {
      const [m, v] = await Promise.all([api.getModels(), api.listPromptVersions()]);
      setModels(m.models);
      setPromptVersions(v);
    } catch { /* silent */ }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const tasks = await api.listBatchTasks();
      setHistoryTasks(tasks);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    if (open) {
      setStep(0);
      setUploadedFile(null);
      setSheets([]);
      setTask(null);
      setTaskId(null);
      form.resetFields();
      loadModelsAndVersions();
      loadHistory();
    }
    return clearPoll;
  }, [open, form, loadModelsAndVersions, loadHistory, clearPoll]);

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const res = await api.uploadBatchExcel(file);
      setUploadedFile(res.filename);
      setSheets(res.sheets);
      if (res.sheets.length > 0) {
        form.setFieldsValue({ sheet_name: res.sheets[0].name });
      }
      message.success(`已上传: ${res.filename}`);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '上传失败');
    } finally {
      setUploading(false);
    }
    return false;
  };

  const startPoll = useCallback((tid: string) => {
    clearPoll();
    pollRef.current = setInterval(async () => {
      try {
        const t = await api.getBatchTask(tid);
        setTask(t);
        if (t.status === 'completed' || t.status === 'cancelled' || t.status === 'error') {
          clearPoll();
          setStep(2);
        }
      } catch { /* silent */ }
    }, 3000);
  }, [clearPoll]);

  const handleStart = async () => {
    if (!uploadedFile) return;
    try {
      const values = await form.validateFields();
      setStarting(true);
      const config = {
        filename: uploadedFile,
        query_col: values.query_col || 'B',
        start_row: values.start_row || 2,
        end_row: values.end_row,
        content_col: values.content_col || 'F',
        tool_col: values.tool_col || 'G',
        model: values.model,
        prompt_version_id: values.prompt_version_id || null,
        sheet_name: values.sheet_name || null,
      };
      const res = await api.startBatchRun(config);
      setTaskId(res.task_id);
      setStep(1);
      message.success(`任务已启动，共 ${res.total} 条`);
      startPoll(res.task_id);
    } catch (e: unknown) {
      if (e instanceof Error) message.error(e.message);
    } finally {
      setStarting(false);
    }
  };

  const handleCancel = async () => {
    if (!taskId) return;
    try {
      await api.cancelBatchTask(taskId);
      message.info('已取消');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '取消失败');
    }
  };

  const handleResumeTask = (t: BatchTask) => {
    setTaskId(t.id);
    setTask(t);
    if (t.status === 'running' || t.status === 'queued') {
      setStep(1);
      startPoll(t.id);
    } else {
      setStep(2);
    }
  };

  const summary = task ? {
    success: task.results.filter((r) => r.status === 'done').length,
    error: task.results.filter((r) => r.status === 'error').length,
    skipped: task.results.filter((r) => r.status === 'skipped').length,
    total: task.total,
  } : null;

  const resultColumns = [
    { title: '行号', dataIndex: 'row', key: 'row', width: 60 },
    { title: 'Query', dataIndex: 'query', key: 'query', width: 200, ellipsis: true },
    { title: 'Content', dataIndex: 'content', key: 'content', ellipsis: true },
    { title: 'Tools', dataIndex: 'tool_calls', key: 'tool_calls', width: 120, ellipsis: true },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (s: string) => {
        const colorMap: Record<string, string> = { done: 'green', error: 'red', skipped: 'default', running: 'blue' };
        return <Tag color={colorMap[s] || 'default'}>{s}</Tag>;
      },
    },
  ];

  const inputStyle: React.CSSProperties = {
    background: 'var(--jf-bg-deep)',
    border: '1px solid var(--jf-border)',
    color: 'var(--jf-text)',
  };

  const content = (
    <>
      <Steps
        current={step}
        size="small"
        style={{ marginBottom: 20 }}
        items={[
          { title: '配置' },
          { title: '运行中' },
          { title: '完成' },
        ]}
      />

      {/* Step 0: Configure */}
      {step === 0 && (
        <div>
          <Dragger
            accept=".xlsx,.xls"
            showUploadList={false}
            beforeUpload={handleUpload}
            disabled={uploading}
            style={{
              background: 'var(--jf-bg-deep)',
              border: '1px dashed var(--jf-border)',
              borderRadius: 'var(--jf-radius-md)',
              marginBottom: 16,
            }}
          >
            <p style={{ color: 'var(--jf-legacy)', fontSize: 32 }}><InboxOutlined /></p>
            <p style={{ color: 'var(--jf-text)' }}>
              {uploading ? '上传中...' : uploadedFile ? `已上传: ${uploadedFile}` : '拖拽 Excel 文件到此处'}
            </p>
          </Dragger>

          {sheets.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              {sheets.map((s) => (
                <Tag key={s.name} style={{ marginBottom: 4 }}>
                  {s.name}: {s.row_count} 行, 列: {s.headers.join(', ')}
                </Tag>
              ))}
            </div>
          )}

          <Form form={form} layout="vertical" initialValues={{ query_col: 'B', start_row: 2, content_col: 'F', tool_col: 'G' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
              <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>Query 列</Text>} name="query_col">
                <Input style={inputStyle} />
              </Form.Item>
              <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>起始行</Text>} name="start_row">
                <InputNumber style={{ ...inputStyle, width: '100%' }} min={1} />
              </Form.Item>
              <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>结束行</Text>} name="end_row">
                <InputNumber style={{ ...inputStyle, width: '100%' }} min={1} />
              </Form.Item>
              <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>Content 列</Text>} name="content_col">
                <Input style={inputStyle} />
              </Form.Item>
              <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>Tool 列</Text>} name="tool_col">
                <Input style={inputStyle} />
              </Form.Item>
              <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>Sheet</Text>} name="sheet_name">
                <Select
                  options={sheets.map((s) => ({ label: s.name, value: s.name }))}
                  style={{ width: '100%' }}
                  allowClear
                />
              </Form.Item>
              <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>模型</Text>} name="model">
                <Select
                  options={models.map((m) => ({ label: m.name, value: m.id }))}
                  style={{ width: '100%' }}
                  allowClear
                />
              </Form.Item>
              <Form.Item label={<Text style={{ color: 'var(--jf-text-muted)' }}>Prompt 版本</Text>} name="prompt_version_id">
                <Select
                  options={promptVersions.map((v) => ({ label: v.label || v.id, value: v.id }))}
                  style={{ width: '100%' }}
                  allowClear
                />
              </Form.Item>
            </div>
          </Form>

          <Space style={{ marginTop: 8 }}>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              disabled={!uploadedFile}
              loading={starting}
              onClick={handleStart}
            >
              开始运行
            </Button>
          </Space>

          {historyTasks.length > 0 && (
            <div style={{ marginTop: 20 }}>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12, marginBottom: 8, display: 'block' }}>
                历史任务
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={loadHistory} style={{ color: 'var(--jf-text-muted)', marginLeft: 4 }} />
              </Text>
              {historyTasks.slice(0, 5).map((t) => (
                <div
                  key={t.id}
                  style={{
                    background: 'var(--jf-bg-deep)',
                    border: '1px solid var(--jf-border)',
                    borderRadius: 'var(--jf-radius-md)',
                    padding: '8px 12px',
                    marginBottom: 6,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    cursor: 'pointer',
                  }}
                  onClick={() => handleResumeTask(t)}
                >
                  <div>
                    <Text style={{ color: 'var(--jf-text)', fontSize: 12 }}>{t.id.slice(0, 8)}</Text>
                    <Text style={{ color: 'var(--jf-text-muted)', fontSize: 11, marginLeft: 8 }}>
                      {fmtUserTime(t.created_at, 'datetime')}
                    </Text>
                  </div>
                  <Tag color={
                    t.status === 'completed' ? 'green' :
                    t.status === 'running' ? 'blue' :
                    t.status === 'error' ? 'red' : 'default'
                  }>
                    {t.status} ({t.completed}/{t.total})
                  </Tag>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Step 1: Running */}
      {step === 1 && task && (
        <div>
          <Progress
            percent={task.total ? Math.round((task.completed / task.total) * 100) : 0}
            strokeColor="var(--jf-legacy)"
            style={{ marginBottom: 12 }}
          />
          <Text style={{ color: 'var(--jf-text-muted)', fontSize: 13 }}>
            进度: {task.completed} / {task.total}
          </Text>
          {task.current_query && (
            <div style={{
              background: 'var(--jf-bg-deep)',
              border: '1px solid var(--jf-border)',
              borderRadius: 'var(--jf-radius-md)',
              padding: '8px 12px',
              marginTop: 8,
              marginBottom: 12,
            }}>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 11 }}>当前 Query:</Text>
              <br />
              <Text style={{ color: 'var(--jf-text)', fontSize: 12 }} ellipsis>
                {task.current_query}
              </Text>
            </div>
          )}
          <Table
            dataSource={task.results.slice(-20).reverse()}
            columns={resultColumns}
            rowKey="row"
            size="small"
            pagination={false}
            scroll={{ y: 300 }}
            style={{ marginBottom: 12 }}
          />
          <Button icon={<StopOutlined />} danger onClick={handleCancel}>取消任务</Button>
        </div>
      )}

      {/* Step 1 loading state (no task yet) */}
      {step === 1 && !task && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin tip="正在获取任务状态..." />
        </div>
      )}

      {/* Step 2: Complete */}
      {step === 2 && task && (
        <div>
          {summary && (
            <div style={{ display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
              {([
                ['成功', summary.success, '#00b894'],
                ['失败', summary.error, '#e74c3c'],
                ['跳过', summary.skipped, '#f39c12'],
                ['总计', summary.total, 'var(--jf-legacy)'],
              ] as const).map(([label, count, color]) => (
                <div key={label} style={{
                  background: 'var(--jf-bg-deep)',
                  border: '1px solid var(--jf-border)',
                  borderRadius: 'var(--jf-radius-md)',
                  padding: '12px 20px',
                  textAlign: 'center',
                  minWidth: 80,
                }}>
                  <Title level={3} style={{ color, margin: 0 }}>{count}</Title>
                  <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12 }}>{label}</Text>
                </div>
              ))}
            </div>
          )}

          {task.error && (
            <div style={{
              background: 'rgba(231,76,60,0.1)',
              border: '1px solid #e74c3c',
              borderRadius: 'var(--jf-radius-md)',
              padding: '8px 12px',
              marginBottom: 12,
            }}>
              <Text style={{ color: '#e74c3c', fontSize: 12 }}>{task.error}</Text>
            </div>
          )}

          <Table
            dataSource={task.results}
            columns={resultColumns}
            rowKey="row"
            size="small"
            pagination={{ pageSize: 20, size: 'small' }}
            scroll={{ y: 400 }}
            style={{ marginBottom: 12 }}
          />

          <Space>
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              onClick={() => {
                if (taskId) window.open(api.batchDownloadUrl(taskId), '_blank');
              }}
            >
              下载结果
            </Button>
            <Button onClick={() => { setStep(0); setTask(null); setTaskId(null); }}>
              新任务
            </Button>
          </Space>
        </div>
      )}
    </>
  );

  if (inline) {
    return <div style={{ padding: '16px 20px', height: '100%', overflow: 'auto' }}>{content}</div>;
  }

  return (
    <Modal
      open={open}
      onCancel={() => { clearPoll(); onClose(); }}
      title={
        <Space>
          <ThunderboltOutlined style={{ color: 'var(--jf-legacy)' }} />
          <span>批量运行</span>
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

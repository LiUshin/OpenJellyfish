import { useState, useEffect, useMemo } from 'react';
import {
  App, Tabs, Button, Tag, Modal, Form, Input, Select, Checkbox,
  Space, Typography, Empty, Spin, Popconfirm, Pagination,
} from 'antd';
import {
  Plus, PencilSimple, Trash, PlayCircle,
  ArrowsClockwise, CaretRight, Info, Clock,
  GearSix, ListDashes, CheckCircle, XCircle,
} from '@phosphor-icons/react';
import {
  listSchedulerTasks, getSchedulerTask, createSchedulerTask,
  updateSchedulerTask, deleteSchedulerTask, runSchedulerTaskNow,
  listServices, getToken,
} from '../../services/api';
import { fmtUserTime, getTzOffset } from '../../utils/timezone';

const { TextArea } = Input;

/* ────────────────────── Types ────────────────────── */

interface TaskPermissions {
  read_dirs?: string[];
  write_dirs?: string[];
}

interface TaskConfig {
  script_path?: string;
  script_args?: string[];
  prompt?: string;
  doc_path?: string | string[];
  capabilities?: string[];
  permissions?: TaskPermissions;
}

interface ReplyTo {
  channel?: string;
  session_id?: string;
}

interface StepData {
  type: string;
  ts?: string;
  content?: string;
  tool?: string;
  args_preview?: string;
  result_preview?: string;
  actions?: unknown[];
  prompt?: string;
  args?: string[];
  doc_paths?: string[];
  capabilities?: string[];
  read_dirs?: string[];
  write_dirs?: string[];
  resolved_write_dirs?: unknown;
  scripts_dir?: string;
  fs_dir?: string;
}

interface RunData {
  status: string;
  started_at?: string;
  finished_at?: string;
  steps?: StepData[];
  output?: string;
}

interface TaskData {
  id: string;
  name: string;
  description?: string;
  task_type: string;
  schedule_type: string;
  schedule?: string;
  enabled?: boolean;
  created_at?: string;
  last_run_at?: string;
  next_run_at?: string;
  run_count?: number;
  reply_to?: ReplyTo;
  task_config?: TaskConfig;
  runs?: RunData[];
  service_id?: string;
  _scope?: 'admin' | 'service';
}

/* ────────────────────── Constants ────────────────────── */

const C = {
  bgPrimary: 'var(--jf-bg-deep)',
  bgSecondary: 'var(--jf-bg-panel)',
  bgTertiary: 'var(--jf-bg-raised)',
  bgHover: 'var(--jf-menu-hover-bg)',
  bgActive: 'var(--jf-menu-selected-bg)',
  border: 'var(--jf-border)',
  borderStrong: 'var(--jf-border-strong)',
  textPrimary: 'var(--jf-text)',
  textSecondary: 'var(--jf-text-muted)',
  textMuted: 'var(--jf-text-dim)',
  primary: 'var(--jf-primary)',
  secondary: 'var(--jf-secondary)',
  accent: 'var(--jf-accent)',
  success: 'var(--jf-success)',
  danger: 'var(--jf-error)',
  warning: 'var(--jf-warning)',
  info: 'var(--jf-accent)',
  mono: "'Cascadia Code', 'Fira Code', 'Consolas', monospace",
};

const STEP_ICONS: Record<string, string> = {
  start: '🚀', ai_message: '🤖', tool_call: '🔧', tool_result: '📦',
  auto_approve: '✅', error: '❌', stdout: '📄', stderr: '⚠️',
  exit: '🏁', finish: '🎉', loop: '🔄', docs_loaded: '📚', reply: '📬',
};

const STEP_LABELS: Record<string, string> = {
  start: '启动', ai_message: 'AI 输出', tool_call: '工具调用', tool_result: '工具返回',
  auto_approve: '自动审批', error: '错误', stdout: '标准输出', stderr: '标准错误',
  exit: '退出', finish: '完成', loop: '执行循环', docs_loaded: '文档加载', reply: '消息推送',
};

const STEP_STYLES: Record<string, { bg: string; fg: string }> = {
  start:        { bg: 'rgba(var(--jf-accent-rgb), 0.2)',  fg: C.info },
  ai_message:   { bg: 'rgba(var(--jf-secondary-rgb), 0.2)',  fg: C.accent },
  tool_call:    { bg: 'rgba(var(--jf-warning-rgb), 0.2)',   fg: C.warning },
  tool_result:  { bg: 'rgba(var(--jf-success-rgb), 0.15)',   fg: C.success },
  auto_approve: { bg: 'rgba(var(--jf-success-rgb), 0.2)',    fg: C.success },
  error:        { bg: 'rgba(var(--jf-error-rgb), 0.2)',    fg: C.danger },
  stdout:       { bg: 'rgba(var(--jf-accent-rgb), 0.15)',  fg: C.info },
  stderr:       { bg: 'rgba(var(--jf-warning-rgb), 0.15)',  fg: C.warning },
  exit:         { bg: 'rgba(148,148,168,0.15)', fg: C.textSecondary },
  finish:       { bg: 'rgba(var(--jf-success-rgb), 0.2)',    fg: C.success },
  loop:         { bg: 'rgba(148,148,168,0.1)',  fg: C.textMuted },
  docs_loaded:  { bg: 'rgba(var(--jf-accent-rgb), 0.15)',  fg: C.info },
  reply:        { bg: 'rgba(var(--jf-secondary-rgb), 0.15)',  fg: C.accent },
};

const SCHEDULE_LABELS: Record<string, string> = {
  once: '⏱ 一次性', cron: '🔄 Cron', interval: '⏳ 间隔',
};
const TYPE_LABELS: Record<string, string> = {
  script: '🔧 脚本', agent: '🤖 Agent',
};

/* ────────────────────── Helpers ────────────────────── */

function fmtTime(iso?: string): string {
  return fmtUserTime(iso, 'datetime');
}

function calcDuration(start?: string, end?: string): string {
  if (!start || !end) return '';
  try {
    const ms = new Date(end).getTime() - new Date(start).getTime();
    if (ms < 1000) return ms + 'ms';
    const s = Math.floor(ms / 1000);
    if (s < 60) return s + 's';
    return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
  } catch { return ''; }
}

async function svcRequest<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${getToken()}` };
  const init: RequestInit = { method, headers };
  if (body) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(`/api${path}`, init);
  if (res.status === 401) {
    localStorage.removeItem('token');
    window.location.reload();
  }
  if (!res.ok) {
    const e = await res.json().catch(() => ({})) as { detail?: string };
    throw new Error(e.detail || '请求失败');
  }
  const text = await res.text();
  return text ? JSON.parse(text) : (undefined as T);
}

function taskApiPath(t: TaskData): string {
  if (t._scope === 'service' && t.service_id)
    return `/scheduler/services/${t.service_id}/${t.id}`;
  return `/scheduler/${t.id}`;
}

/* ────────────────────── Sub-components ────────────────────── */

function ExpandableText({ text, maxH = 80, danger = false }: {
  text: string; maxH?: number; danger?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      onClick={() => { if (!expanded) setExpanded(true); }}
      style={{
        fontFamily: C.mono, fontSize: 11, color: C.textSecondary,
        background: C.bgSecondary, padding: '6px 8px', borderRadius: 'var(--jf-radius-md)',
        maxHeight: expanded ? 'none' : maxH,
        overflow: expanded ? 'auto' : 'hidden',
        cursor: expanded ? 'default' : 'pointer',
        position: 'relative',
        whiteSpace: 'pre-wrap', wordBreak: 'break-all', marginTop: 4,
        ...(danger ? { borderLeft: `2px solid ${C.danger}` } : {}),
      }}
    >
      {text}
      {!expanded && (
        <div style={{
          position: 'absolute', bottom: 0, left: 0, right: 0,
          padding: '4px 8px', textAlign: 'center',
          fontSize: 10, color: C.textMuted,
          background: `linear-gradient(transparent, ${C.bgSecondary} 60%)`,
        }}>
          点击展开…
        </div>
      )}
    </div>
  );
}

function StepView({ step }: { step: StepData }) {
  const tp = step.type || 'start';
  const icon = STEP_ICONS[tp] || '•';
  const label = STEP_LABELS[tp] || tp;
  const sc = STEP_STYLES[tp] || { bg: 'rgba(148,148,168,0.1)', fg: C.textMuted };
  const ts = fmtUserTime(step.ts, 'time');

  const meta = (txt: string) => (
    <div style={{ fontSize: 10, color: C.textMuted, marginTop: 2 }}>{txt}</div>
  );

  let detail: React.ReactNode = null;
  let hasExpandable = false;

  switch (tp) {
    case 'tool_call':
      detail = (
        <>
          {meta(`工具: ${step.tool || ''}`)}
          {step.args_preview && <ExpandableText text={step.args_preview} />}
        </>
      );
      hasExpandable = !!step.args_preview;
      break;
    case 'tool_result':
      detail = (
        <>
          {meta(`工具: ${step.tool || ''}`)}
          {step.result_preview && <ExpandableText text={step.result_preview} />}
        </>
      );
      hasExpandable = !!step.result_preview;
      break;
    case 'ai_message':
      if (step.content) { detail = <ExpandableText text={step.content} />; hasExpandable = true; }
      break;
    case 'stdout':
    case 'stderr':
      if (step.content) { detail = <ExpandableText text={step.content} />; hasExpandable = true; }
      break;
    case 'error':
      if (step.content) { detail = <ExpandableText text={step.content} danger maxH={160} />; hasExpandable = true; }
      break;
    case 'auto_approve':
      if (step.actions) detail = meta(`${(step.actions as unknown[]).length} 个操作已自动审批`);
      break;
    case 'start':
      detail = (
        <>
          {step.prompt && meta(`指令: ${step.prompt}`)}
          {!!step.args?.length && meta(`参数: ${step.args.join(' ')}`)}
          {step.doc_paths?.length && step.doc_paths[0]
            ? meta(`文档: ${step.doc_paths.join(', ')}`) : null}
          {!!step.capabilities?.length && meta(`能力: ${step.capabilities.join(', ')}`)}
          {!!step.read_dirs?.length && meta(`📖 读: ${step.read_dirs.join(', ')}`)}
          {!!step.write_dirs?.length && meta(`📝 写: ${step.write_dirs.join(', ')}`)}
          {step.resolved_write_dirs && (
            <ExpandableText
              text={`resolved_write: ${JSON.stringify(step.resolved_write_dirs)}\nscripts_dir: ${step.scripts_dir || ''}\nfs_dir: ${step.fs_dir || ''}`}
            />
          )}
        </>
      );
      hasExpandable = !!step.resolved_write_dirs;
      break;
  }

  const showFallback = !!step.content && !hasExpandable && tp !== 'start';

  return (
    <div style={{
      display: 'flex', gap: 10, padding: '8px 12px',
      borderBottom: `1px solid ${C.border}`, fontSize: 12, lineHeight: 1.6,
    }}>
      <div style={{
        width: 22, height: 22, borderRadius: '50%',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 11, flexShrink: 0, marginTop: 1,
        background: sc.bg, color: sc.fg,
      }}>
        {icon}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontWeight: 500, color: C.textPrimary }}>{label}</span>
          <span style={{ fontSize: 10, color: C.textMuted, whiteSpace: 'nowrap' }}>{ts}</span>
        </div>
        {detail}
        {showFallback && meta(step.content!)}
      </div>
    </div>
  );
}

function RunCard({ run }: { run: RunData }) {
  const [open, setOpen] = useState(false);
  const statusMap: Record<string, { text: string; color: string; icon: React.ReactNode }> = {
    success: { text: '成功', color: C.success, icon: <CheckCircle size={18} weight="fill" color={C.success} /> },
    timeout: { text: '超时', color: C.warning, icon: <Clock size={18} weight="fill" color={C.warning} /> },
    error:   { text: '失败', color: C.danger, icon: <XCircle size={18} weight="fill" color={C.danger} /> },
    running: { text: '运行中', color: C.info, icon: <ArrowsClockwise size={18} weight="fill" color={C.info} /> },
  };
  const st = statusMap[run.status] || { text: run.status, color: C.textMuted, icon: null };
  const dur = calcDuration(run.started_at, run.finished_at);

  return (
    <div style={{ position: 'relative', marginBottom: 24, paddingLeft: 28 }}>
      {/* Timeline dot */}
      <div style={{
        position: 'absolute', left: 0, top: 2,
        width: 20, height: 20, borderRadius: '50%',
        background: C.bgPrimary, border: `2px solid ${st.color}`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1,
      }}>
        {st.icon}
      </div>
      {/* Content */}
      <div style={{
        border: `1px solid ${run.status === 'error' ? 'rgba(var(--jf-error-rgb), 0.25)' : C.border}`,
        borderRadius: 'var(--jf-radius-lg)', background: C.bgPrimary, overflow: 'hidden',
      }}>
        <div
          onClick={() => setOpen(v => !v)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '10px 14px', cursor: 'pointer', transition: 'background 0.2s',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = C.bgHover; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <CaretRight
              size={12}
              weight="bold"
              color={C.textMuted}
              style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 0.2s' }}
            />
            <span style={{ fontSize: 13, fontWeight: 600, color: st.color }}>{st.text}</span>
            {dur && <span style={{ fontSize: 12, color: C.textMuted }}>({dur})</span>}
          </div>
          <span style={{ fontSize: 11, color: C.textMuted }}>
            {fmtTime(run.started_at)}
          </span>
        </div>
        {open && (
          <div style={{ borderTop: `1px solid ${C.border}`, padding: 12 }}>
            {run.steps?.map((s, i) => {
              const tp = s.type || 'start';
              const icon = STEP_ICONS[tp] || '•';
              const label = STEP_LABELS[tp] || tp;
              const sc = STEP_STYLES[tp] || { bg: 'rgba(148,148,168,0.1)', fg: C.textMuted };
              const ts = fmtUserTime(s.ts, 'time');
              return (
                <div key={i} style={{
                  display: 'flex', alignItems: 'flex-start', gap: 8, padding: '4px 0',
                  fontSize: 12, color: C.textSecondary,
                }}>
                  <span style={{
                    width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 10, background: sc.bg, color: sc.fg,
                  }}>{icon}</span>
                  <span style={{ fontWeight: 500, color: C.textPrimary, minWidth: 60 }}>{label}</span>
                  {ts && <span style={{ color: C.textMuted, fontSize: 10 }}>{ts}</span>}
                  {s.content && tp === 'error' && (
                    <code style={{
                      flex: 1, fontFamily: C.mono, fontSize: 11, color: C.danger,
                      background: 'rgba(var(--jf-error-rgb), 0.08)', padding: '2px 6px', borderRadius: 'var(--jf-radius-sm)',
                      wordBreak: 'break-all',
                    }}>{s.content.slice(0, 200)}</code>
                  )}
                  {s.tool && <span style={{ color: C.textMuted }}>{s.tool}</span>}
                </div>
              );
            })}
            {run.output && (
              <pre style={{
                fontFamily: C.mono, fontSize: 11, color: C.textSecondary,
                background: C.bgSecondary, padding: '8px 12px', borderRadius: 'var(--jf-radius-md)',
                maxHeight: 150, overflow: 'auto', whiteSpace: 'pre-wrap',
                wordBreak: 'break-all', margin: '8px 0 0',
              }}>{run.output}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ────────────────────── Main Component ────────────────────── */

export default function SchedulerPage() {
  const { message: msg } = App.useApp();
  const [form] = Form.useForm();

  const [tasks, setTasks] = useState<TaskData[]>([]);
  const [serviceTasks, setServiceTasks] = useState<TaskData[]>([]);
  const [currentTask, setCurrentTask] = useState<TaskData | null>(null);
  const [activeTab, setActiveTab] = useState('admin');
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [availableServices, setAvailableServices] = useState<{ id: string; name: string }[]>([]);
  const [taskSearch, setTaskSearch] = useState('');
  const [svcPage, setSvcPage] = useState(1);
  const SVC_PAGE_SIZE = 20;

  useEffect(() => {
    listServices()
      .then((svcs) => setAvailableServices((svcs as { id: string; name: string; published?: boolean }[]).filter((s) => s.published !== false)))
      .catch(() => {});
  }, []);

  const watchedTaskType = Form.useWatch('task_type', form);
  const watchedSchedType = Form.useWatch('schedule_type', form);
  const taskType = watchedTaskType ?? 'script';
  const scheduleType = watchedSchedType ?? 'once';

  const schedCfg = useMemo(() => {
    switch (scheduleType) {
      case 'cron':
        return { label: 'Cron 表达式', ph: '0 9 * * *', hint: '分 时 日 月 周，如 "0 9 * * 1" = 每周一早 9 点' };
      case 'interval':
        return { label: '间隔秒数', ph: '3600', hint: '如 3600 = 每小时，86400 = 每天' };
      default:
        return { label: '执行时间', ph: '2026-04-01T09:00:00', hint: 'ISO 时间格式，如 2026-04-01T09:00:00' };
    }
  }, [scheduleType]);

  /* ── Data loading ── */

  const loadTasks = async () => {
    setLoading(true);
    try {
      setTasks(await listSchedulerTasks() as TaskData[]);
    } catch (e: unknown) { msg.error((e as Error).message); }
    finally { setLoading(false); }
  };

  const loadServiceTasks = async () => {
    try {
      setServiceTasks(await svcRequest<TaskData[]>('GET', '/scheduler/services/all'));
    } catch (e: unknown) { msg.error((e as Error).message); }
  };

  useEffect(() => { loadTasks(); loadServiceTasks(); }, []);

  /* ── Task selection ── */

  const selectTask = async (id: string) => {
    setDetailLoading(true);
    try {
      const t = await getSchedulerTask(id) as TaskData;
      t._scope = 'admin';
      setCurrentTask(t);
    } catch (e: unknown) { msg.error((e as Error).message); }
    finally { setDetailLoading(false); }
  };

  const selectServiceTask = async (svcId: string, taskId: string) => {
    setDetailLoading(true);
    try {
      const t = await svcRequest<TaskData>('GET', `/scheduler/services/${svcId}/${taskId}`);
      t._scope = 'service';
      setCurrentTask(t);
    } catch (e: unknown) { msg.error((e as Error).message); }
    finally { setDetailLoading(false); }
  };

  /* ── CRUD handlers ── */

  const openCreateModal = () => {
    form.resetFields();
    if (activeTab === 'service') {
      form.setFieldsValue({ task_type: 'agent', schedule_type: 'once', enabled: true, target_service: undefined });
    } else {
      form.setFieldsValue({ task_type: 'script', schedule_type: 'once', enabled: true });
    }
    setEditingId(null);
    setModalOpen(true);
  };

  const openEditModal = () => {
    if (!currentTask) return;
    const t = currentTask;
    const cfg = t.task_config || {};
    const perms = cfg.permissions || {};
    const dp = cfg.doc_path;
    form.resetFields();
    form.setFieldsValue({
      name: t.name,
      description: t.description,
      task_type: t.task_type || 'script',
      schedule_type: t.schedule_type || 'once',
      schedule: t.schedule,
      script_path: cfg.script_path,
      script_args: (cfg.script_args || []).join(','),
      agent_prompt: cfg.prompt,
      doc_paths: Array.isArray(dp) ? dp.join(',') : (dp || ''),
      capabilities: cfg.capabilities || [],
      read_dirs: perms.read_dirs || [],
      write_dirs: perms.write_dirs || [],
      enabled: t.enabled !== false,
    });
    setEditingId(t.id);
    setModalOpen(true);
  };

  const handleSave = async () => {
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch { return; }

    setSaving(true);
    try {
      const tc: Record<string, unknown> = {};
      if (values.task_type === 'script') {
        tc.script_path = values.script_path;
        const a = ((values.script_args as string) || '')
          .split(',').map(s => s.trim()).filter(Boolean);
        if (a.length) tc.script_args = a;
      } else {
        tc.prompt = values.agent_prompt;
        const docs = ((values.doc_paths as string) || '')
          .split(',').map(s => s.trim()).filter(Boolean);
        if (docs.length) tc.doc_path = docs;
        if ((values.capabilities as string[])?.length)
          tc.capabilities = values.capabilities;
      }
      const rd = values.read_dirs as string[] | undefined;
      const wd = values.write_dirs as string[] | undefined;
      if (rd?.length || wd?.length) {
        const p: Record<string, string[]> = {};
        if (rd?.length) p.read_dirs = rd;
        if (wd?.length) p.write_dirs = wd;
        tc.permissions = p;
      }

      const body = {
        name: values.name,
        description: (values.description as string) || '',
        task_type: values.task_type,
        schedule_type: values.schedule_type,
        schedule: values.schedule,
        task_config: tc,
        enabled: values.enabled ?? true,
        // Cron/once 的「本地时间」与设置页时区一致；须显式写入，避免旧任务缺字段时被当成 UTC
        tz_offset_hours: getTzOffset(),
      };

      let savedId = editingId;
      if (editingId) {
        if (currentTask?._scope === 'service' && currentTask.service_id) {
          await svcRequest('PUT', taskApiPath(currentTask), body);
        } else {
          await updateSchedulerTask(editingId, body);
        }
        msg.success('任务已更新');
      } else if (activeTab === 'service' && values.target_service) {
        const svcId = values.target_service as string;
        const created = await svcRequest<TaskData>('POST', `/scheduler/services/${svcId}`, body);
        savedId = created.id;
        msg.success('服务任务已创建');
      } else {
        const created = await createSchedulerTask(body) as TaskData;
        savedId = created.id;
        msg.success('任务已创建');
      }

      setModalOpen(false);
      if (activeTab === 'service') await loadServiceTasks();
      else await loadTasks();
      if (savedId && activeTab === 'admin') selectTask(savedId);
    } catch (e: unknown) {
      msg.error((e as Error).message);
    } finally { setSaving(false); }
  };

  const handleDelete = async () => {
    if (!currentTask) return;
    try {
      if (currentTask._scope === 'service' && currentTask.service_id) {
        await svcRequest('DELETE', taskApiPath(currentTask));
      } else {
        await deleteSchedulerTask(currentTask.id);
      }
      msg.success('已删除');
      setCurrentTask(null);
      if (activeTab === 'service') await loadServiceTasks();
      else await loadTasks();
    } catch (e: unknown) { msg.error((e as Error).message); }
  };

  const handleRunNow = async () => {
    if (!currentTask) return;
    try {
      if (currentTask._scope === 'service' && currentTask.service_id) {
        await svcRequest('POST', `${taskApiPath(currentTask)}/run-now`);
      } else {
        await runSchedulerTaskNow(currentTask.id);
      }
      msg.success('任务已触发，稍后可在运行记录中查看结果');
    } catch (e: unknown) { msg.error((e as Error).message); }
  };

  const handleRefreshRuns = async () => {
    if (!currentTask) return;
    setDetailLoading(true);
    try {
      let fresh: TaskData;
      if (currentTask._scope === 'service' && currentTask.service_id) {
        fresh = await svcRequest('GET', taskApiPath(currentTask));
      } else {
        fresh = await getSchedulerTask(currentTask.id) as TaskData;
      }
      fresh._scope = currentTask._scope;
      setCurrentTask(fresh);
    } catch (e: unknown) { msg.error((e as Error).message); }
    finally { setDetailLoading(false); }
  };

  /* ── Sidebar list ── */

  const filteredTasks = useMemo(() => {
    const source = activeTab === 'admin' ? tasks : serviceTasks;
    if (!taskSearch.trim()) return source;
    const q = taskSearch.toLowerCase();
    return source.filter(t =>
      (t.name || '').toLowerCase().includes(q) ||
      (t.description || '').toLowerCase().includes(q) ||
      (t.id || '').toLowerCase().includes(q) ||
      (t.service_id || '').toLowerCase().includes(q) ||
      (t.task_config?.prompt || '').toLowerCase().includes(q)
    );
  }, [activeTab, tasks, serviceTasks, taskSearch]);

  const visibleTasks = useMemo(() => {
    if (activeTab !== 'service') return filteredTasks;
    const start = (svcPage - 1) * SVC_PAGE_SIZE;
    return filteredTasks.slice(start, start + SVC_PAGE_SIZE);
  }, [activeTab, filteredTasks, svcPage]);

  const renderTaskList = () => {
    if (!visibleTasks.length) {
      const totalSource = activeTab === 'admin' ? tasks : serviceTasks;
      const noResults = taskSearch.trim() && totalSource.length > 0;
      return (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={noResults ? '没有匹配的任务' : (activeTab === 'admin' ? '暂无管理员任务' : '暂无服务任务')}
          style={{ padding: '40px 0' }}
        />
      );
    }
    return visibleTasks.map(t => {
      const active = currentTask?.id === t.id;
      return (
        <div
          key={t.id + (t.service_id || '')}
          onClick={() =>
            t.service_id
              ? selectServiceTask(t.service_id, t.id)
              : selectTask(t.id)
          }
          style={{
            padding: 12, borderRadius: 'var(--jf-radius-lg)', cursor: 'pointer', marginBottom: 4,
            background: active ? C.bgActive : 'transparent',
            transition: 'background 0.2s',
          }}
          onMouseEnter={e => {
            if (!active) e.currentTarget.style.background = C.bgHover;
          }}
          onMouseLeave={e => {
            if (!active) e.currentTarget.style.background = 'transparent';
          }}
        >
          <div style={{
            fontWeight: 500, fontSize: 14, marginBottom: 2,
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <Tag
              color={t.enabled !== false ? 'success' : 'default'}
              style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}
            >
              {t.enabled !== false ? '启用' : '停用'}
            </Tag>
            <span style={{
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {t.name}
            </span>
          </div>
          <div style={{
            fontSize: 12, color: C.textSecondary,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {t.description || SCHEDULE_LABELS[t.schedule_type] || t.schedule_type}
          </div>
          <div style={{
            fontSize: 11, color: C.textMuted, marginTop: 4,
            display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
          }}>
            <Tag
              color={t.task_type === 'agent' ? 'purple' : 'blue'}
              style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}
            >
              {TYPE_LABELS[t.task_type] || t.task_type}
            </Tag>
            {t.schedule && <span>{t.schedule}</span>}
            {t.service_id && (
              <Tag style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
                {t.service_id.slice(0, 12)}
              </Tag>
            )}
            {!!t.run_count && <span>{t.run_count} 次运行</span>}
            {t.reply_to && <span>📬</span>}
          </div>
        </div>
      );
    });
  };

  /* ── Detail panel ── */

  const infoRow = (label: string, value: React.ReactNode) => (
    <div style={{ display: 'flex', padding: '6px 0' }}>
      <div style={{ color: C.textSecondary, width: 100, flexShrink: 0, fontSize: 13 }}>
        {label}
      </div>
      <div style={{ flex: 1, minWidth: 0, fontSize: 13 }}>{value}</div>
    </div>
  );

  const sectionTitle = (title: string, extra?: React.ReactNode) => (
    <div style={{
      fontSize: 14, fontWeight: 600, marginBottom: 12, paddingBottom: 8,
      borderBottom: `1px solid ${C.border}`,
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    }}>
      {title}
      {extra}
    </div>
  );

  const codeSpan = (text: string) => (
    <code style={{ fontFamily: C.mono, fontSize: 12 }}>{text}</code>
  );

  const renderDetail = () => {
    if (!currentTask) return null;
    const t = currentTask;
    const cfg = t.task_config || {};
    const perms = cfg.permissions || {};
    const runs = [...(t.runs || [])].reverse();

    const docPaths = Array.isArray(cfg.doc_path)
      ? cfg.doc_path : cfg.doc_path ? [cfg.doc_path] : [];

    return (
      <>
        {/* Header */}
        <div style={{
          padding: '16px 24px', borderBottom: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <Typography.Title level={4} style={{ margin: 0, color: C.textPrimary }}>
            {t.name}
          </Typography.Title>
          <Space>
            <Button
              type="primary"
              icon={<PlayCircle size={16} weight="fill" />}
              style={{ background: C.success, borderColor: C.success, color: '#000' }}
              onClick={handleRunNow}
            >
              立即运行
            </Button>
            <Button icon={<PencilSimple size={16} />} onClick={openEditModal}>编辑</Button>
            <Popconfirm
              title={`确定删除「${t.name}」？`}
              onConfirm={handleDelete}
              okText="确定"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button danger icon={<Trash size={16} />}>删除</Button>
            </Popconfirm>
          </Space>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
          <Spin spinning={detailLoading}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {/* 基本信息 */}
            <div style={{
              background: C.bgSecondary, border: `1px solid ${C.border}`,
              borderRadius: 'var(--jf-radius-lg)', overflow: 'hidden',
            }}>
              <div style={{
                padding: '14px 20px', borderBottom: `1px solid ${C.border}`,
                fontWeight: 600, fontSize: 14, display: 'flex', alignItems: 'center', gap: 8,
              }}>
                <Info size={16} color={C.accent} /> 基本信息
              </div>
              <div style={{ padding: 20 }}>
              {infoRow('ID', codeSpan(t.id))}
              {t.service_id && infoRow('所属服务', (
                <Space>
                  {codeSpan(t.service_id)}
                  <Tag color="warning">服务任务</Tag>
                </Space>
              ))}
              {infoRow('描述', t.description || '—')}
              {infoRow('状态', (
                <Tag color={t.enabled !== false ? 'success' : 'default'}>
                  {t.enabled !== false ? '启用' : '停用'}
                </Tag>
              ))}
              {infoRow('创建时间', fmtTime(t.created_at))}
              {infoRow('上次运行', fmtTime(t.last_run_at) || '从未')}
              {t.reply_to && infoRow('📬 结果推送', (
                <>
                  {t.reply_to.channel === 'wechat' ? '微信' : t.reply_to.channel || '—'}
                  {t.reply_to.session_id && (
                    <> {codeSpan(t.reply_to.session_id.slice(0, 12))}</>
                  )}
                </>
              ))}
              </div>
            </div>

            {/* 调度配置 */}
            <div style={{
              background: C.bgSecondary, border: `1px solid ${C.border}`,
              borderRadius: 'var(--jf-radius-lg)', overflow: 'hidden',
            }}>
              <div style={{
                padding: '14px 20px', borderBottom: `1px solid ${C.border}`,
                fontWeight: 600, fontSize: 14, display: 'flex', alignItems: 'center', gap: 8,
              }}>
                <Clock size={16} color={C.accent} /> 调度配置
              </div>
              <div style={{ padding: 20 }}>
              {infoRow('调度方式', SCHEDULE_LABELS[t.schedule_type] || t.schedule_type)}
              {infoRow('调度值', codeSpan(t.schedule || ''))}
              {infoRow('下次运行', fmtTime(t.next_run_at) || '无')}
              </div>
            </div>

            {/* 任务配置 */}
            <div style={{
              background: C.bgSecondary, border: `1px solid ${C.border}`,
              borderRadius: 'var(--jf-radius-lg)', overflow: 'hidden',
            }}>
              <div style={{
                padding: '14px 20px', borderBottom: `1px solid ${C.border}`,
                fontWeight: 600, fontSize: 14, display: 'flex', alignItems: 'center', gap: 8,
              }}>
                <GearSix size={16} color={C.accent} /> 任务配置
              </div>
              <div style={{ padding: 20 }}>
              {infoRow('类型', (
                <Tag color={t.task_type === 'agent' ? 'purple' : 'blue'}>
                  {TYPE_LABELS[t.task_type] || t.task_type}
                </Tag>
              ))}
              {t.task_type === 'script' && (
                <>
                  {infoRow('脚本', codeSpan(cfg.script_path || ''))}
                  {!!cfg.script_args?.length &&
                    infoRow('参数', codeSpan(cfg.script_args.join(' ')))}
                </>
              )}
              {t.task_type === 'agent' && (
                <>
                  {infoRow('指令', cfg.prompt || '—')}
                  {docPaths.length > 0 && docPaths[0] &&
                    infoRow('参考文档', docPaths.map((p, i) => (
                      <code key={i} style={{ fontFamily: C.mono, fontSize: 12, marginRight: 6 }}>
                        {p}
                      </code>
                    )))
                  }
                  {!!cfg.capabilities?.length &&
                    infoRow('Agent 能力', cfg.capabilities.join(', '))}
                </>
              )}
              {!!perms.read_dirs?.length &&
                infoRow('📖 可读目录', codeSpan(perms.read_dirs.join(', ')))}
              {!!perms.write_dirs?.length &&
                infoRow('📝 可写目录', codeSpan(perms.write_dirs.join(', ')))}
              {!perms.read_dirs?.length && !perms.write_dirs?.length &&
                infoRow('📂 权限', (
                  <span style={{ color: C.textMuted }}>
                    默认 (docs, scripts, generated, tasks)
                  </span>
                ))}
              </div>
            </div>

            {/* 运行记录 */}
            <div style={{
              background: C.bgSecondary, border: `1px solid ${C.border}`,
              borderRadius: 'var(--jf-radius-lg)', overflow: 'hidden',
            }}>
              <div style={{
                padding: '14px 20px', borderBottom: `1px solid ${C.border}`,
                fontWeight: 600, fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <ListDashes size={16} color={C.accent} /> 运行记录
                </span>
                <Button size="small" type="text" icon={<ArrowsClockwise size={14} />} onClick={handleRefreshRuns}>
                  刷新
                </Button>
              </div>
              <div style={{ padding: 20 }}>
              {!runs.length ? (
                <Typography.Text style={{ color: C.textMuted, fontSize: 13 }}>
                  暂无运行记录
                </Typography.Text>
              ) : (
                <div style={{ position: 'relative', paddingLeft: 10 }}>
                  <div style={{
                    position: 'absolute', left: 19, top: 0, bottom: 0,
                    width: 2, background: C.border, zIndex: 0,
                  }} />
                  {runs.map((r, i) => <RunCard key={i} run={r} />)}
                </div>
              )}
              </div>
            </div>
            </div>
          </Spin>
        </div>
      </>
    );
  };

  /* ── Render ── */

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0, height: '100%' }}>
      {/* ── Sidebar ── */}
      <div style={{
        width: 300, minWidth: 260, background: C.bgSecondary,
        borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <div style={{
          padding: '7px 16px', height: 47, boxSizing: 'border-box',
          borderBottom: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <Typography.Title
            level={5}
            style={{ margin: 0, color: C.textPrimary, display: 'flex', alignItems: 'center', gap: 8 }}
          >
            ⏰ 定时任务
          </Typography.Title>
        </div>

        <Tabs
          activeKey={activeTab}
          onChange={tab => { setActiveTab(tab); setCurrentTask(null); setTaskSearch(''); setSvcPage(1); }}
          centered
          items={[
            { key: 'admin', label: '管理员任务' },
            { key: 'service', label: `服务任务${serviceTasks.length ? ` (${serviceTasks.length})` : ''}` },
          ]}
          style={{ padding: '0 8px' }}
        />

        <div style={{ padding: '0 12px 8px' }}>
          <Input
            placeholder="搜索任务…"
            prefix={<span style={{ color: C.textMuted, fontSize: 13 }}>🔍</span>}
            value={taskSearch}
            onChange={e => { setTaskSearch(e.target.value); setSvcPage(1); }}
            allowClear
            size="small"
            style={{ background: C.bgPrimary, border: `1px solid ${C.border}`, color: C.textPrimary }}
          />
        </div>

        <div style={{ padding: '0 12px 8px' }}>
          <Button
            type="primary"
            icon={<Plus size={16} weight="bold" />}
            onClick={openCreateModal}
            block
          >
            创建任务
          </Button>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
          <Spin spinning={loading}>{renderTaskList()}</Spin>
        </div>

        {activeTab === 'service' && filteredTasks.length > SVC_PAGE_SIZE && (
          <div style={{
            padding: '8px 12px', borderTop: `1px solid ${C.border}`,
            display: 'flex', justifyContent: 'center',
          }}>
            <Pagination
              current={svcPage}
              total={filteredTasks.length}
              pageSize={SVC_PAGE_SIZE}
              onChange={p => setSvcPage(p)}
              size="small"
              showSizeChanger={false}
              simple
            />
          </div>
        )}
      </div>

      {/* ── Main Panel ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {currentTask ? renderDetail() : (
          <div style={{
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            height: '100%', color: C.textMuted,
          }}>
            <div style={{ fontSize: 48, marginBottom: 16, opacity: 0.4 }}>📋</div>
            <Typography.Text style={{ color: C.textMuted }}>
              选择或创建一个定时任务
            </Typography.Text>
          </div>
        )}
      </div>

      {/* ── Create / Edit Modal ── */}
      <Modal
        title={editingId ? '编辑任务' : '创建任务'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={560}
        forceRender
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name"
            label="任务名称"
            rules={[{ required: true, message: '请填写任务名称' }]}
          >
            <Input placeholder="每日新闻摘要" />
          </Form.Item>

          <Form.Item name="description" label="描述">
            <TextArea placeholder="任务的简要说明" rows={2} />
          </Form.Item>

          {activeTab === 'service' && !editingId && (
            <Form.Item
              name="target_service"
              label="目标 Service"
              rules={[{ required: true, message: '请选择目标 Service' }]}
            >
              <Select
                placeholder="选择一个 Service"
                options={availableServices.map((s) => ({ value: s.id, label: s.name || s.id }))}
                showSearch
                filterOption={(input, opt) =>
                  (opt?.label as string || '').toLowerCase().includes(input.toLowerCase())
                }
              />
            </Form.Item>
          )}

          <Form.Item name="task_type" label="任务类型" rules={[{ required: true }]}>
            <Select
              options={
                activeTab === 'service'
                  ? [{ value: 'agent', label: '🤖 Agent 任务' }]
                  : [
                      { value: 'script', label: '🔧 脚本执行' },
                      { value: 'agent', label: '🤖 Agent 任务' },
                    ]
              }
            />
          </Form.Item>

          <Form.Item name="schedule_type" label="调度方式" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'once', label: '⏱ 一次性（指定时间）' },
                { value: 'cron', label: '🔄 Cron 表达式（周期）' },
                { value: 'interval', label: '⏳ 固定间隔（秒）' },
              ]}
            />
          </Form.Item>

          <Form.Item
            name="schedule"
            label={schedCfg.label}
            rules={[{ required: true, message: '请填写调度值' }]}
            extra={schedCfg.hint}
          >
            <Input placeholder={schedCfg.ph} />
          </Form.Item>

          {/* ── Script fields ── */}
          {taskType === 'script' && (
            <>
              <Form.Item
                name="script_path"
                label="脚本路径"
                rules={[{ required: true, message: '请填写脚本路径' }]}
                extra="相对 scripts/ 目录"
              >
                <Input placeholder="hello.py 或 analysis/run.py" />
              </Form.Item>
              <Form.Item name="script_args" label="脚本参数" extra="每个参数用逗号分隔">
                <Input placeholder="--output,result.json" />
              </Form.Item>
            </>
          )}

          {/* ── Agent fields ── */}
          {taskType === 'agent' && (
            <>
              <Form.Item
                name="agent_prompt"
                label="执行指令"
                rules={[{ required: true, message: '请填写执行指令' }]}
                extra="给 Agent 的任务描述"
              >
                <TextArea placeholder="搜索最新 AI 新闻并生成语音摘要" rows={3} />
              </Form.Item>
              <Form.Item
                name="doc_paths"
                label="参考文档（docs/ 下路径）"
                extra="逗号分隔多个文件路径"
              >
                <Input placeholder="daily_report.md" />
              </Form.Item>
              <Form.Item
                name="capabilities"
                label="Agent 能力"
                extra="Agent 默认拥有联网搜索和脚本执行能力"
              >
                <Checkbox.Group>
                  <Space>
                    <Checkbox value="image">🎨 图片</Checkbox>
                    <Checkbox value="speech">🔊 语音</Checkbox>
                    <Checkbox value="video">🎬 视频</Checkbox>
                  </Space>
                </Checkbox.Group>
              </Form.Item>
            </>
          )}

          {/* ── Permissions ── */}
          <div style={{
            borderTop: `1px solid ${C.border}`,
            paddingTop: 16, marginTop: 8, marginBottom: 16,
          }}>
            <Typography.Text strong style={{ fontSize: 13 }}>
              📂 沙箱权限（相对用户目录）
            </Typography.Text>
          </div>

          <Form.Item
            name="read_dirs"
            label="可读目录"
            extra="默认: docs, scripts, generated, tasks。输入 * 表示全部"
          >
            <Select
              mode="tags"
              placeholder="输入目录名按回车添加"
              tokenSeparators={[',']}
            />
          </Form.Item>

          <Form.Item
            name="write_dirs"
            label="可写目录"
            extra="默认: docs, scripts, generated, tasks。输入 * 表示全部"
          >
            <Select
              mode="tags"
              placeholder="输入目录名按回车添加"
              tokenSeparators={[',']}
            />
          </Form.Item>

          <Form.Item name="enabled" valuePropName="checked">
            <Checkbox>立即启用</Checkbox>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

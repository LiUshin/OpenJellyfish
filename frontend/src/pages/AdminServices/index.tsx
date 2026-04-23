import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Table, Modal, Form, Input, Select, Button, Tag, message,
  Popconfirm, Space, Typography, Checkbox,
  Spin, Empty, InputNumber, Tooltip,
  Drawer, Segmented,
} from 'antd';
import { PlusOutlined, MinusCircleOutlined } from '@ant-design/icons';
import {
  Plus,
  PencilSimple,
  Trash,
  Copy,
  LinkSimple,
  ArrowsClockwise,
  GridFour,
  MagnifyingGlass,
  Globe,
  Timer,
  ImageSquare,
  SpeakerHigh,
  FilmSlate,
  Key,
  ChatCircleDots,
  Lightning,
  Info,
  MagicWand,
  ArrowSquareOut,
  ChartBar,
} from '@phosphor-icons/react';
import FileTreePicker, { PickerTrigger } from '../../components/FileTreePicker';
import {
  listServices, getService, createService, updateService, deleteService,
  getModels, listPromptVersions, listProfileVersions, listServiceKeys,
  createServiceKey, deleteServiceKey, request,
  listServiceConversations, getServiceConversation, deleteServiceConversation,
  listServiceUsage,
} from '../../services/api';
import type {
  ServiceConfig, ModelInfo, PromptVersion,
  ServiceKey, WeChatSession, WeChatMessage,
} from '../../types';
import type {
  ServiceConvSummary, ServiceConvDetail, ServiceUsageRecord,
} from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';
import LogoLoading from '../../components/LogoLoading';

const { Text, Title } = Typography;
const { TextArea } = Input;

/** Brand palette — references CSS custom properties from themes.css */
const C = {
  primary: 'var(--jf-primary)',
  secondary: 'var(--jf-secondary)',
  accent: 'var(--jf-accent)',
  warning: 'var(--jf-warning)',
  error: 'var(--jf-error)',
  bg0: 'var(--jf-bg-deep)',
  bg1: 'var(--jf-bg-panel)',
  bg2: 'var(--jf-bg-raised)',
  text: 'var(--jf-text)',
  muted: 'var(--jf-text-muted)',
  border: 'var(--jf-border)',
  borderStrong: 'var(--jf-border-strong)',
  published: 'var(--jf-success)',
  draftDot: 'var(--jf-text-muted)',
} as const;

const FLOAT_SHADOW = 'var(--jf-shadow-float)';

const CAPABILITY_OPTIONS = [
  { value: 'web', label: '🌐 联网搜索' },
  { value: 'scheduler', label: '⏰ 定时任务' },
  { value: 'image', label: '🎨 图片生成' },
  { value: 'speech', label: '🔊 语音生成' },
  { value: 'video', label: '🎬 视频生成' },
];

const UI_CAPABILITIES = new Set(['web', 'scheduler', 'image', 'speech', 'video']);

const CAPABILITY_LABELS: Record<string, string> = {
  web: '联网搜索', scheduler: '定时任务', image: '图片生成',
  speech: '语音生成', video: '视频生成',
};

const CAPABILITY_ICONS: Record<string, React.ReactNode> = {
  web: <Globe size={16} />,
  scheduler: <Timer size={16} />,
  image: <ImageSquare size={16} />,
  speech: <SpeakerHigh size={16} />,
  video: <FilmSlate size={16} />,
};

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    message.success('已复制');
  } catch {
    message.error('复制失败');
  }
}

function isWcExpired(svc: ServiceConfig): boolean {
  const wc = svc.wechat_channel;
  if (!wc?.enabled || !wc.expires_at) return false;
  try { return new Date(wc.expires_at) < new Date(); }
  catch { return false; }
}

/* ───────── Sub-components ───────── */

function Section({ title, children, extra }: {
  title: string;
  children: React.ReactNode;
  extra?: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{
        fontSize: 14, fontWeight: 600,
        marginBottom: 12, paddingBottom: 8,
        borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        color: C.text,
      }}>
        <span>{title}</span>
        {extra}
      </div>
      {children}
    </div>
  );
}

function ModuleCard({ title, icon, extra, children, accent }: {
  title: string;
  icon?: React.ReactNode;
  extra?: React.ReactNode;
  children: React.ReactNode;
  accent?: string;
}) {
  return (
    <div style={{
      background: C.bg1,
      border: `1px solid ${accent || C.border}`,
      borderRadius: 'var(--jf-radius-lg)',
    }}>
      <div style={{
        padding: '14px 20px',
        borderBottom: `1px solid ${C.border}`,
        fontWeight: 600,
        fontSize: 14,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        color: accent || C.text,
        background: accent ? `${accent}08` : undefined,
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {icon}
          {title}
        </span>
        {extra}
      </div>
      <div style={{ padding: 20 }}>
        {children}
      </div>
    </div>
  );
}

function CopyBox({ value, extra }: { value: string; extra?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      message.success('已复制');
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      message.error('复制失败');
    }
  };

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      background: C.bg0, border: `1px solid ${C.border}`,
      borderRadius: 'var(--jf-radius-md)', padding: '6px 10px',
      boxShadow: FLOAT_SHADOW,
    }}>
      <code style={{
        flex: 1, fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace",
        fontSize: 12, wordBreak: 'break-all', color: C.text,
      }}>
        {value}
      </code>
      <Button
        size="small"
        icon={
          <Copy
            size={16}
            weight="regular"
            color={copied ? C.accent : C.text}
          />
        }
        onClick={handleCopy}
        style={{
          color: copied ? C.accent : C.text,
          borderColor: copied ? C.accent : C.borderStrong,
          background: copied ? 'rgba(var(--jf-accent-rgb), 0.08)' : undefined,
        }}
      >
        复制
      </Button>
      {extra}
    </div>
  );
}

function StatusDot({ published }: { published: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: published ? C.published : C.draftDot,
          flexShrink: 0,
          boxShadow: published ? '0 0 6px rgba(var(--jf-success-rgb), 0.4)' : 'none',
        }}
      />
      <span style={{ fontSize: 11, color: C.muted }}>
        {published ? '已发布' : '草稿'}
      </span>
    </span>
  );
}

function WeChatConfigPanel({ wc, onSave }: {
  wc: NonNullable<ServiceConfig['wechat_channel']>;
  onSave: (expiresAt: string | null, maxSessions: number) => void;
}) {
  const [expires, setExpires] = useState(wc.expires_at?.slice(0, 16) || '');
  const [maxSessions, setMaxSessions] = useState(wc.max_sessions || 100);

  return (
    <div style={{ marginTop: 12 }}>
      <Space align="center" style={{ marginBottom: 10 }}>
        <Text style={{ color: C.muted, minWidth: 80 }}>过期时间</Text>
        <input
          type="datetime-local"
          value={expires}
          onChange={e => setExpires(e.target.value)}
          style={{
            padding: '6px 10px', background: C.bg0, border: `1px solid ${C.border}`,
            borderRadius: 'var(--jf-radius-md)', color: C.text, fontSize: 13, outline: 'none',
          }}
        />
        <Button
          size="small"
          onClick={() => onSave(expires ? new Date(expires).toISOString() : null, maxSessions)}
        >
          保存
        </Button>
        {expires && (
          <Button size="small" type="text" onClick={() => { setExpires(''); onSave(null, maxSessions); }}>
            清除
          </Button>
        )}
      </Space>
      <div>
        <Space align="center">
          <Text style={{ color: C.muted, minWidth: 80 }}>最大会话</Text>
          <InputNumber
            value={maxSessions}
            onChange={v => setMaxSessions(v || 100)}
            min={1} max={1000}
            style={{ width: 80 }}
          />
        </Space>
      </div>
    </div>
  );
}

/**
 * Form.Item 包装版 PickerTrigger：从 Form 接收 value，点击触发外部 onClick 弹出 picker。
 * 外部通过 form.setFieldValue 写回。
 */
function PickerField({
  value,
  onClick,
  placeholder,
}: {
  value?: string[];
  onClick: () => void;
  placeholder?: string;
}) {
  return <PickerTrigger value={value || []} onClick={onClick} placeholder={placeholder} />;
}

/* ───────── Main Page ───────── */

export default function AdminServicesPage() {
  const [services, setServices] = useState<ServiceConfig[]>([]);
  const [currentSvc, setCurrentSvc] = useState<ServiceConfig | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [promptVersions, setPromptVersions] = useState<PromptVersion[]>([]);
  const [profileVersions, setProfileVersions] = useState<PromptVersion[]>([]);
  const [loading, setLoading] = useState(true);

  // service modal
  const [svcModalOpen, setSvcModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [docPickerOpen, setDocPickerOpen] = useState(false);
  const [scriptPickerOpen, setScriptPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  // keys
  const [serviceKeys, setServiceKeys] = useState<ServiceKey[]>([]);
  const [keysLoading, setKeysLoading] = useState(false);

  // key modal
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [generatedKey, setGeneratedKey] = useState<string | null>(null);
  const [keyGenerating, setKeyGenerating] = useState(false);
  const [keyName, setKeyName] = useState('default');

  // wechat
  const [wcSessions, setWcSessions] = useState<WeChatSession[]>([]);
  const [wcLoading, setWcLoading] = useState(false);

  // wechat chat modal
  const [chatModalOpen, setChatModalOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<WeChatMessage[]>([]);
  const [chatTitle, setChatTitle] = useState('');
  const [chatLoading, setChatLoading] = useState(false);

  // 使用情况：consumer 会话历史 + API 调用记录
  const [svcConvs, setSvcConvs] = useState<ServiceConvSummary[]>([]);
  const [svcConvsLoading, setSvcConvsLoading] = useState(false);
  const [svcUsage, setSvcUsage] = useState<ServiceUsageRecord[]>([]);
  const [svcUsageLoading, setSvcUsageLoading] = useState(false);
  const [usageView, setUsageView] = useState<'convs' | 'records'>('convs');
  const [usageChannelFilter, setUsageChannelFilter] = useState<'' | 'web' | 'api' | 'wechat'>('');

  // service consumer 会话查看 Drawer
  const [convDrawerOpen, setConvDrawerOpen] = useState(false);
  const [convDrawerData, setConvDrawerData] = useState<ServiceConvDetail | null>(null);
  const [convDrawerLoading, setConvDrawerLoading] = useState(false);

  const [svcSearch, setSvcSearch] = useState('');

  const filteredServices = useMemo(() => {
    if (!svcSearch.trim()) return services;
    const q = svcSearch.toLowerCase();
    return services.filter(s =>
      (s.name || '').toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q) ||
      (s.id || '').toLowerCase().includes(q)
    );
  }, [services, svcSearch]);

  /* ─── Data Loading ─── */

  const loadAllServices = useCallback(async () => {
    try {
      const data = await listServices() as ServiceConfig[];
      setServices(data);
    } catch (e: unknown) {
      message.error((e as Error).message || '加载 Service 列表失败');
    }
  }, []);

  const loadModelsData = useCallback(async () => {
    try {
      const data = await getModels();
      setModels(data.models || []);
    } catch (e) { console.error('Failed to load models', e); }
  }, []);

  const loadPromptVersionsData = useCallback(async () => {
    try {
      const data = await listPromptVersions();
      setPromptVersions(data);
    } catch (e) { console.error('Failed to load prompt versions', e); }
  }, []);

  const loadProfileVersionsData = useCallback(async () => {
    try {
      const data = await listProfileVersions();
      setProfileVersions(data);
    } catch (e) { console.error('Failed to load profile versions', e); }
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      await Promise.all([loadAllServices(), loadModelsData(), loadPromptVersionsData(), loadProfileVersionsData()]);
      setLoading(false);
    })();
  }, [loadAllServices, loadModelsData, loadPromptVersionsData, loadProfileVersionsData]);

  /* ─── Keys ─── */

  const loadKeys = useCallback(async (serviceId: string) => {
    setKeysLoading(true);
    try {
      const keys = await listServiceKeys(serviceId) as ServiceKey[];
      setServiceKeys(keys);
    } catch (e: unknown) {
      message.error((e as Error).message || '加载 Key 列表失败');
    } finally {
      setKeysLoading(false);
    }
  }, []);

  const handleDeleteKey = async (keyId: string) => {
    if (!currentSvc) return;
    try {
      await deleteServiceKey(currentSvc.id, keyId);
      message.success('Key 已删除');
      loadKeys(currentSvc.id);
    } catch (e: unknown) {
      message.error((e as Error).message);
    }
  };

  const handleGenerateKey = async () => {
    if (!currentSvc) return;
    setKeyGenerating(true);
    try {
      const result = await createServiceKey(currentSvc.id, keyName || 'default');
      setGeneratedKey(result.key);
    } catch (e: unknown) {
      message.error((e as Error).message);
    } finally {
      setKeyGenerating(false);
    }
  };

  /* ─── WeChat ─── */

  const loadWcSessions = useCallback(async (serviceId: string) => {
    setWcLoading(true);
    try {
      const sessions = await request<WeChatSession[]>('GET', `/wc/${serviceId}/sessions`);
      setWcSessions(sessions);
    } catch {
      setWcSessions([]);
    } finally {
      setWcLoading(false);
    }
  }, []);

  const handleToggleWeChat = async () => {
    if (!currentSvc) return;
    const wc = currentSvc.wechat_channel || { enabled: false };
    const newEnabled = !wc.enabled;
    try {
      await request('PUT', `/wc/${currentSvc.id}/config`, {
        enabled: newEnabled,
        expires_at: wc.expires_at || null,
        max_sessions: wc.max_sessions || 100,
      });
      const updated = await getService(currentSvc.id) as ServiceConfig;
      setCurrentSvc(updated);
      setServices(prev => prev.map(s => s.id === updated.id ? updated : s));
      message.success(newEnabled ? '微信渠道已启用' : '微信渠道已禁用');
      if (newEnabled) loadWcSessions(currentSvc.id);
    } catch (e: unknown) {
      message.error((e as Error).message);
    }
  };

  const handleSaveWcConfig = async (expiresAt: string | null, maxSessions: number) => {
    if (!currentSvc) return;
    try {
      await request('PUT', `/wc/${currentSvc.id}/config`, {
        enabled: true,
        expires_at: expiresAt,
        max_sessions: maxSessions,
      });
      const updated = await getService(currentSvc.id) as ServiceConfig;
      setCurrentSvc(updated);
      setServices(prev => prev.map(s => s.id === updated.id ? updated : s));
      message.success('配置已保存');
    } catch (e: unknown) {
      message.error((e as Error).message);
    }
  };

  const handleDisconnectSession = async (sessionId: string) => {
    if (!currentSvc) return;
    try {
      await request('DELETE', `/wc/${currentSvc.id}/sessions/${sessionId}`);
      message.success('会话已断开');
      loadWcSessions(currentSvc.id);
    } catch (e: unknown) {
      message.error((e as Error).message);
    }
  };

  const handleViewChat = async (sessionId: string) => {
    if (!currentSvc) return;
    setChatTitle(`对话记录 — ${sessionId}`);
    setChatModalOpen(true);
    setChatLoading(true);
    try {
      const data = await request<{ messages: WeChatMessage[] }>(
        'GET', `/wc/${currentSvc.id}/sessions/${sessionId}/messages`,
      );
      setChatMessages(data.messages || []);
    } catch (e: unknown) {
      message.error((e as Error).message);
    } finally {
      setChatLoading(false);
    }
  };

  /* ─── 使用情况：会话历史 + 调用记录 ─── */

  const loadSvcConvs = useCallback(async (sid: string) => {
    setSvcConvsLoading(true);
    try {
      setSvcConvs(await listServiceConversations(sid));
    } catch {
      setSvcConvs([]);
    } finally {
      setSvcConvsLoading(false);
    }
  }, []);

  const loadSvcUsage = useCallback(async (sid: string,
                                          channel?: '' | 'web' | 'api' | 'wechat') => {
    setSvcUsageLoading(true);
    try {
      const r = await listServiceUsage(sid, {
        limit: 200,
        channel: channel || undefined,
      });
      setSvcUsage(r.records);
    } catch {
      setSvcUsage([]);
    } finally {
      setSvcUsageLoading(false);
    }
  }, []);

  const openSvcConvDrawer = useCallback(async (sid: string, cid: string) => {
    setConvDrawerOpen(true);
    setConvDrawerLoading(true);
    setConvDrawerData(null);
    try {
      const data = await getServiceConversation(sid, cid);
      setConvDrawerData(data);
    } catch (e: unknown) {
      message.error((e as Error).message);
    } finally {
      setConvDrawerLoading(false);
    }
  }, []);

  const handleDeleteSvcConv = async (sid: string, cid: string) => {
    try {
      await deleteServiceConversation(sid, cid);
      message.success('已删除');
      loadSvcConvs(sid);
    } catch (e: unknown) {
      message.error((e as Error).message);
    }
  };

  /* ─── Service CRUD ─── */

  const selectService = (svc: ServiceConfig) => {
    setCurrentSvc(svc);
    loadKeys(svc.id);
    if (svc.wechat_channel?.enabled) {
      loadWcSessions(svc.id);
    } else {
      setWcSessions([]);
    }
    loadSvcConvs(svc.id);
    loadSvcUsage(svc.id);
  };

  const openCreateModal = () => {
    setEditingId(null);
    form.resetFields();
    form.setFieldsValue({
      name: '', description: '',
      model: models[0]?.id || '',
      system_prompt_version_id: undefined,
      user_profile_version_id: undefined,
      allowed_docs: ['*'], allowed_scripts: [],
      capabilities: [], published: true,
      welcome_message: '',
      quick_questions: [],
    });
    setSvcModalOpen(true);
  };

  const openEditModal = () => {
    if (!currentSvc) return;
    setEditingId(currentSvc.id);
    form.setFieldsValue({
      name: currentSvc.name,
      description: currentSvc.description || '',
      model: currentSvc.model || '',
      system_prompt_version_id: currentSvc.system_prompt_version_id || undefined,
      user_profile_version_id: currentSvc.user_profile_version_id || undefined,
      allowed_docs: currentSvc.allowed_docs && currentSvc.allowed_docs.length > 0
        ? currentSvc.allowed_docs : ['*'],
      allowed_scripts: currentSvc.allowed_scripts || [],
      capabilities: (currentSvc.capabilities || []).filter(c => UI_CAPABILITIES.has(c)),
      published: currentSvc.published !== false,
      welcome_message: currentSvc.welcome_message || '',
      quick_questions: currentSvc.quick_questions || [],
    });
    setSvcModalOpen(true);
  };

  const handleSaveService = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      const hiddenCaps = editingId
        ? (currentSvc?.capabilities || []).filter(c => !UI_CAPABILITIES.has(c))
        : [];

      const cleanQuestions = (values.quick_questions || [])
        .map((q: string) => (q || '').trim())
        .filter((q: string) => q.length > 0);

      const body = {
        name: values.name,
        description: values.description || '',
        model: values.model,
        system_prompt_version_id: values.system_prompt_version_id || null,
        user_profile_version_id: values.user_profile_version_id || null,
        allowed_docs: Array.isArray(values.allowed_docs) && values.allowed_docs.length > 0
          ? values.allowed_docs : ['*'],
        allowed_scripts: Array.isArray(values.allowed_scripts) ? values.allowed_scripts : [],
        capabilities: [...(values.capabilities || []), ...hiddenCaps],
        published: values.published,
        welcome_message: values.welcome_message || '',
        quick_questions: cleanQuestions,
      };

      let savedId: string;
      if (editingId) {
        await updateService(editingId, body);
        savedId = editingId;
        message.success('Service 已更新');
      } else {
        const created = await createService(body) as ServiceConfig;
        savedId = created.id;
        message.success('Service 已创建');
      }

      setSvcModalOpen(false);
      await loadAllServices();
      const updatedSvc = await getService(savedId) as ServiceConfig;
      selectService(updatedSvc);
    } catch (e: unknown) {
      if (e && typeof e === 'object' && 'errorFields' in e) return;
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteService = async () => {
    if (!currentSvc) return;
    try {
      await deleteService(currentSvc.id);
      message.success('Service 已删除');
      setCurrentSvc(null);
      setServiceKeys([]);
      await loadAllServices();
    } catch (e: unknown) {
      message.error((e as Error).message);
    }
  };

  /* ─── Table columns ─── */

  const keyColumns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '前缀', dataIndex: 'prefix', key: 'prefix',
      render: (v: string) => (
        <code style={{ color: C.secondary, fontFamily: "'Cascadia Code',monospace", fontSize: 12 }}>
          {v}...
        </code>
      ),
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at',
      render: (v: string) => fmtUserTime(v, 'short') || '-',
    },
    {
      title: '最近使用', dataIndex: 'last_used_at', key: 'last_used_at',
      render: (v: string) => fmtUserTime(v, 'short') || '从未',
    },
    {
      title: '', key: 'actions', width: 100,
      render: (_: unknown, record: ServiceKey) => (
        <Popconfirm title="确定删除此 Key？" onConfirm={() => handleDeleteKey(record.id)}>
          <Button type="text" danger size="small" icon={<Trash size={16} />}>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  const wcSessionColumns = [
    {
      title: '会话 ID', dataIndex: 'session_id', key: 'session_id',
      render: (v: string) => (
        <Tooltip title={v}>
          <code style={{ fontSize: 11, fontFamily: "'Cascadia Code',monospace", color: C.text }}>
            {v.length > 12 ? v.slice(0, 12) + '…' : v}
          </code>
        </Tooltip>
      ),
    },
    {
      title: '对话 ID', dataIndex: 'conversation_id', key: 'conversation_id',
      render: (v: string) => (
        <Tooltip title={v}>
          <code style={{ fontSize: 11, fontFamily: "'Cascadia Code',monospace", color: C.text }}>
            {v.length > 12 ? v.slice(0, 12) + '…' : v}
          </code>
        </Tooltip>
      ),
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at',
      render: (v: string) => fmtUserTime(v, 'short') || '-',
    },
    {
      title: '最近活跃', dataIndex: 'last_active_at', key: 'last_active_at',
      render: (v: string) => fmtUserTime(v, 'short') || '-',
    },
    {
      title: '', key: 'actions', width: 140,
      render: (_: unknown, record: WeChatSession) => (
        <Space size={4}>
          <Button size="small" onClick={() => handleViewChat(record.session_id)}>查看</Button>
          <Popconfirm
            title="确定断开此微信会话？用户将无法继续对话。"
            onConfirm={() => handleDisconnectSession(record.session_id)}
          >
            <Button size="small" danger>断开</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  /* ─── 使用情况 table 列 ─── */

  const SOURCE_LABELS: Record<string, string> = {
    web: '网页', api: 'API', wechat: '微信', '': '未知',
  };
  const SOURCE_COLORS: Record<string, string> = {
    web: 'blue', api: 'purple', wechat: 'green', '': 'default',
  };

  const svcConvColumns = [
    {
      title: '来源', dataIndex: 'source', key: 'source', width: 80,
      render: (s: string) => (
        <Tag color={SOURCE_COLORS[s] ?? 'default'}>{SOURCE_LABELS[s] ?? s}</Tag>
      ),
    },
    {
      title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (t: string, r: ServiceConvSummary) =>
        t ? (
          <span>{t}</span>
        ) : (
          <span style={{ color: C.muted, fontSize: 12 }}>
            (无标题 · {r.id.slice(0, 8)})
          </span>
        ),
    },
    {
      title: '消息', dataIndex: 'message_count', key: 'message_count',
      width: 70, align: 'right' as const,
    },
    {
      title: '最近活跃', dataIndex: 'updated_at', key: 'updated_at', width: 140,
      render: (v: string) => fmtUserTime(v, 'short') || '-',
    },
    {
      title: '', key: 'actions', width: 130,
      render: (_: unknown, r: ServiceConvSummary) => (
        <Space size={4}>
          <Button
            size="small"
            onClick={() => currentSvc && openSvcConvDrawer(currentSvc.id, r.id)}
          >
            查看
          </Button>
          <Popconfirm
            title="删除此会话？历史消息和上传文件都会被清掉，无法恢复。"
            onConfirm={() => currentSvc && handleDeleteSvcConv(currentSvc.id, r.id)}
          >
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const svcUsageColumns = [
    {
      title: '时间', dataIndex: 'ts', key: 'ts', width: 150,
      render: (v: string) => fmtUserTime(v, 'short') || v,
    },
    {
      title: '来源', dataIndex: 'channel', key: 'channel', width: 70,
      render: (c: string) => (
        <Tag color={SOURCE_COLORS[c] ?? 'default'}>{SOURCE_LABELS[c] ?? c}</Tag>
      ),
    },
    {
      title: 'Endpoint', dataIndex: 'endpoint', key: 'endpoint', ellipsis: true,
      render: (e: string) => (
        <code style={{ fontSize: 11, fontFamily: "'Cascadia Code',monospace", color: C.text }}>
          {e}
        </code>
      ),
    },
    {
      title: 'Key', dataIndex: 'key_id', key: 'key_id', width: 110,
      render: (v: string) =>
        v ? (
          <code style={{ fontSize: 11, fontFamily: "'Cascadia Code',monospace", color: C.muted }}>
            {v}
          </code>
        ) : (
          <span style={{ color: C.muted }}>-</span>
        ),
    },
    {
      title: '会话', dataIndex: 'conv_id', key: 'conv_id', width: 100,
      render: (cid: string) =>
        cid ? (
          <Button
            type="link" size="small"
            style={{ padding: 0, fontSize: 11 }}
            onClick={() => currentSvc && openSvcConvDrawer(currentSvc.id, cid)}
          >
            {cid.slice(0, 8)}
          </Button>
        ) : (
          <span style={{ color: C.muted }}>-</span>
        ),
    },
    {
      title: '状态', dataIndex: 'status_code', key: 'status_code', width: 70,
      render: (sc: number, r: ServiceUsageRecord) => (
        <Tag color={r.ok ? 'success' : 'error'}>{sc}</Tag>
      ),
    },
    {
      title: '耗时', dataIndex: 'latency_ms', key: 'latency_ms',
      width: 80, align: 'right' as const,
      render: (ms: number) => `${ms} ms`,
    },
  ];

  /* ─── WeChat detail render ─── */

  const renderWeChatContent = () => {
    if (!currentSvc) return null;
    const wc = currentSvc.wechat_channel;

    if (!wc?.enabled) {
      return (
        <Text style={{ color: C.muted, fontSize: 13 }}>
          启用后，用户可通过微信扫码与此 Service 对话。每个扫码用户将获得独立的对话。
        </Text>
      );
    }

    const apiOrigin = window.location.origin;
    const scanUrl = `${apiOrigin}/wc/${currentSvc.id}`;
    const expired = isWcExpired(currentSvc);

    return (
      <div>
        <WeChatConfigPanel
          key={`${wc.expires_at || ''}-${wc.max_sessions || 100}`}
          wc={wc}
          onSave={handleSaveWcConfig}
        />

        {/* scan URL */}
        <div style={{
          margin: '14px 0', padding: 16,
          background: C.bg0, border: `1px solid ${C.border}`, borderRadius: 'var(--jf-radius-md)',
          display: 'flex', alignItems: 'center', gap: 16,
          boxShadow: FLOAT_SHADOW,
        }}>
          <span style={{ fontSize: 36 }}>💬</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, color: C.muted, marginBottom: 6 }}>
              扫码入口（分享给用户）
            </div>
            <CopyBox
              value={scanUrl}
              extra={
                <Button
                  size="small" icon={<LinkSimple size={16} />}
                  onClick={() => window.open(scanUrl, '_blank')}
                >
                  打开
                </Button>
              }
            />
          </div>
        </div>

        {expired && (
          <Text style={{ display: 'block', marginTop: 8, fontSize: 13, color: C.warning }}>
            ⚠ 微信渠道已过期，用户无法扫码。请更新过期时间或清除限制。
          </Text>
        )}

        {/* sessions */}
        <div style={{ marginTop: 14 }}>
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            marginBottom: 6,
          }}>
            <Text strong style={{ fontSize: 13, color: C.text }}>活跃会话</Text>
            <Button
              size="small" type="text" icon={<ArrowsClockwise size={16} />}
              onClick={() => loadWcSessions(currentSvc.id)}
            >
              刷新
            </Button>
          </div>
          <Table
            dataSource={wcSessions}
            columns={wcSessionColumns}
            rowKey="session_id"
            size="small"
            loading={wcLoading}
            pagination={false}
            locale={{ emptyText: '暂无活跃会话' }}
            onRow={(_, index) => ({
              style: {
                background: (index ?? 0) % 2 === 0 ? C.bg1 : C.bg2,
              },
            })}
          />
          {wcSessions.length > 0 && (
            <Text style={{ fontSize: 11, color: C.muted, marginTop: 6, display: 'block' }}>
              {wcSessions.length} 个活跃会话
            </Text>
          )}
        </div>
      </div>
    );
  };

  /* ─── Render ─── */

  if (loading) {
    return <LogoLoading size={240} />;
  }

  const apiOrigin = window.location.origin;

  const modalStyles = {
    content: { borderRadius: 'var(--jf-radius-lg)', overflow: 'hidden' as const, padding: 0 },
    header: { borderRadius: 'var(--jf-radius-lg) var(--jf-radius-lg) 0 0' },
    body: { padding: '16px 24px 24px' },
  };

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0, height: '100%', width: '100%', background: C.bg0 }}>
      {/* ── Service list 30% ── */}
      <div style={{
        flex: '0 0 30%',
        maxWidth: '40%',
        minWidth: 260,
        background: C.bg1,
        borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}
      >
        <div style={{
          padding: 16,
          borderBottom: `1px solid ${C.border}`,
        }}
        >
          <Title level={4} style={{ color: C.text, margin: '0 0 12px', fontSize: 16 }}>
            Service 管理
          </Title>
          <div style={{
            background: C.bg0, border: `1px solid ${C.border}`,
            borderRadius: 'var(--jf-radius-md)', padding: '6px 10px',
            display: 'flex', alignItems: 'center', gap: 8,
            marginBottom: 12,
          }}>
            <MagnifyingGlass size={14} color={C.muted} />
            <input
              type="text"
              placeholder="搜索 Service..."
              value={svcSearch}
              onChange={e => setSvcSearch(e.target.value)}
              style={{
                background: 'transparent', border: 'none', outline: 'none',
                color: C.text, width: '100%', fontSize: 13,
              }}
            />
          </div>
          <Button
            type="primary"
            icon={<Plus size={18} weight="bold" />}
            block
            onClick={openCreateModal}
            style={{ background: C.primary, borderColor: C.primary, color: C.bg0 }}
          >
            创建 Service
          </Button>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
          {filteredServices.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无 Service"
              style={{ marginTop: 60 }}
            />
          ) : (
            filteredServices.map(svc => {
              const active = currentSvc?.id === svc.id;
              const published = svc.published !== false;
              return (
                <div
                  key={svc.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => selectService(svc)}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') selectService(svc); }}
                  style={{
                    padding: 12,
                    borderRadius: 'var(--jf-radius-md)',
                    cursor: 'pointer',
                    marginBottom: 10,
                    background: C.bg2,
                    border: `1px solid ${active ? C.primary : C.border}`,
                    borderLeft: active ? `3px solid ${C.primary}` : `1px solid ${C.border}`,
                    boxShadow: FLOAT_SHADOW,
                    transition: 'border-color 0.2s, box-shadow 0.2s',
                  }}
                  onMouseEnter={e => {
                    if (!active) {
                      const el = e.currentTarget;
                      el.style.borderColor = C.primary;
                      el.style.borderLeftWidth = '1px';
                      el.style.borderLeftColor = C.primary;
                    }
                  }}
                  onMouseLeave={e => {
                    if (!active) {
                      const el = e.currentTarget;
                      el.style.borderColor = C.border;
                      el.style.borderLeft = `1px solid ${C.border}`;
                    }
                  }}
                >
                  <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 4, color: C.text }}>
                    {svc.name}
                  </div>
                  <div style={{
                    fontSize: 12, color: C.muted,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}
                  >
                    {svc.description || '无描述'}
                  </div>
                  <div style={{
                    fontSize: 11, marginTop: 8,
                    display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
                  }}
                  >
                    <StatusDot published={published} />
                    <span style={{ color: C.muted }}>{(svc.model || '').split(':').pop()}</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* ── Detail panel 70% ── */}
      <div style={{
        flex: '1 1 70%',
        minWidth: 0, minHeight: 0,
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
        background: C.bg0,
      }}
      >
        {!currentSvc ? (
          <div style={{
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            height: '100%', color: C.muted,
          }}
          >
            <GridFour size={48} color={C.muted} style={{ opacity: 0.45, marginBottom: 16 }} />
            <Text style={{ color: C.muted }}>选择或创建一个 Service</Text>
          </div>
        ) : (
          <>
            {/* Detail Header */}
            <div style={{
              padding: '20px 32px',
              borderBottom: `1px solid ${C.border}`,
              background: C.bg1,
              display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
            }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                  <Title level={4} style={{ color: C.text, margin: 0, fontSize: 20 }}>
                    {currentSvc.name}
                  </Title>
                  <Tag
                    style={{
                    borderColor: currentSvc.published !== false ? C.published : C.border,
                    color: currentSvc.published !== false ? C.published : C.muted,
                    background: currentSvc.published !== false ? 'rgba(var(--jf-success-rgb), 0.08)' : undefined,
                    }}
                  >
                    {currentSvc.published !== false ? '已发布' : '草稿'}
                  </Tag>
                </div>
                <div style={{ color: C.muted, fontSize: 13 }}>
                  ID: <code style={{ fontFamily: "'Cascadia Code',monospace", color: C.accent }}>{currentSvc.id}</code>
                </div>
              </div>
              <Space>
                <Button icon={<PencilSimple size={16} />} onClick={openEditModal}>编辑</Button>
                <Popconfirm title={`确定删除「${currentSvc.name}」？不可恢复。`} onConfirm={handleDeleteService}>
                  <Button danger icon={<Trash size={16} />}>删除</Button>
                </Popconfirm>
              </Space>
            </div>

            {/* Scrollable Modules */}
            <div style={{ flex: '1 1 0%', minHeight: 0, overflowY: 'auto', padding: '24px 32px', display: 'flex', flexDirection: 'column', gap: 24 }}>

              {/* Stats Row */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
                <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 'var(--jf-radius-lg)', padding: 16, display: 'flex', alignItems: 'center', gap: 16 }}>
                  <div style={{ width: 40, height: 40, borderRadius: 'var(--jf-radius-md)', background: 'rgba(var(--jf-accent-rgb), 0.09)', color: C.accent, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <ChatCircleDots size={20} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: C.muted }}>微信会话</div>
                    <div style={{ fontSize: 20, fontWeight: 600 }}>{currentSvc.wechat_channel?.enabled ? wcSessions.length : '—'}</div>
                  </div>
                </div>
                <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 'var(--jf-radius-lg)', padding: 16, display: 'flex', alignItems: 'center', gap: 16 }}>
                  <div style={{ width: 40, height: 40, borderRadius: 'var(--jf-radius-md)', background: 'rgba(var(--jf-primary-rgb), 0.09)', color: C.primary, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Key size={20} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: C.muted }}>API Keys</div>
                    <div style={{ fontSize: 20, fontWeight: 600 }}>{serviceKeys.length}</div>
                  </div>
                </div>
                <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 'var(--jf-radius-lg)', padding: 16, display: 'flex', alignItems: 'center', gap: 16 }}>
                  <div style={{ width: 40, height: 40, borderRadius: 'var(--jf-radius-md)', background: 'rgba(var(--jf-success-rgb), 0.09)', color: C.published, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Lightning size={20} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: C.muted }}>挂载能力</div>
                    <div style={{ fontSize: 20, fontWeight: 600 }}>{(currentSvc.capabilities || []).filter(c => UI_CAPABILITIES.has(c)).length}</div>
                  </div>
                </div>
              </div>

              {/* Module: Basic Config */}
              <ModuleCard title="基本配置" icon={<Info size={16} />}>
                <div style={{ display: 'grid', gridTemplateColumns: '100px 1fr', gap: '12px 16px', fontSize: 13 }}>
                  <div style={{ color: C.muted }}>模型</div>
                  <div>{currentSvc.model || '—'}</div>
                  <div style={{ color: C.muted }}>描述</div>
                  <div>{currentSvc.description || '—'}</div>
                  <div style={{ color: C.muted }}>允许文档</div>
                  <div><code style={{ fontFamily: 'monospace', background: C.bg0, padding: '2px 6px', borderRadius: 'var(--jf-radius-sm)', border: `1px solid ${C.border}`, color: C.primary, fontSize: 12 }}>{(currentSvc.allowed_docs || []).join(', ') || '—'}</code></div>
                  <div style={{ color: C.muted }}>允许脚本</div>
                  <div><code style={{ fontFamily: 'monospace', background: C.bg0, padding: '2px 6px', borderRadius: 'var(--jf-radius-sm)', border: `1px solid ${C.border}`, color: C.primary, fontSize: 12 }}>{(currentSvc.allowed_scripts || []).join(', ') || '无'}</code></div>
                  <div style={{ color: C.muted }}>创建时间</div>
                  <div>{fmtUserTime(currentSvc.created_at, 'datetime') || '—'}</div>
                </div>
              </ModuleCard>

              {/* Module: Capabilities */}
              <ModuleCard title="挂载能力" icon={<MagicWand size={16} />}>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  {['web', 'scheduler', 'image', 'speech', 'video'].map(cap => {
                    const active = (currentSvc.capabilities || []).includes(cap);
                    return (
                      <div key={cap} style={{
                        background: C.bg0,
                        border: `1px solid ${active ? C.borderStrong : C.border}`,
                        borderStyle: active ? 'solid' : 'dashed',
                        padding: '10px 16px',
                        borderRadius: 'var(--jf-radius-md)',
                        display: 'flex', alignItems: 'center', gap: 8,
                        fontSize: 13, color: active ? C.text : C.muted,
                        opacity: active ? 1 : 0.5,
                      }}>
                        <span style={{ color: active ? C.accent : C.muted }}>{CAPABILITY_ICONS[cap]}</span>
                        {CAPABILITY_LABELS[cap] || cap}
                        {!active && <span style={{ fontSize: 11 }}>(未启用)</span>}
                      </div>
                    );
                  })}
                </div>
              </ModuleCard>

              {/* Module: API Keys */}
              <ModuleCard
                title="API Keys"
                icon={<Key size={16} />}
                extra={
                  <Button
                    type="primary" size="small" icon={<Plus size={14} weight="bold" />}
                    onClick={() => { setGeneratedKey(null); setKeyName('default'); setKeyModalOpen(true); }}
                    style={{ background: C.primary, borderColor: C.primary }}
                  >
                    创建 Key
                  </Button>
                }
              >
                <Table
                  dataSource={serviceKeys}
                  columns={keyColumns}
                  rowKey="id"
                  size="small"
                  loading={keysLoading}
                  pagination={false}
                  locale={{ emptyText: '暂无 API Key' }}
                  onRow={(_, index) => ({
                    style: { background: (index ?? 0) % 2 === 0 ? C.bg1 : C.bg2 },
                  })}
                />
              </ModuleCard>

              {/* Module: WeChat */}
              <ModuleCard
                title={currentSvc.wechat_channel?.enabled ? '微信渠道 (运行中)' : '微信渠道'}
                icon={<ChatCircleDots size={16} />}
                accent={currentSvc.wechat_channel?.enabled && !isWcExpired(currentSvc) ? '#07c160' : undefined}
                extra={
                  <Space size={8}>
                    {currentSvc.wechat_channel?.enabled && (
                      <Tag color={isWcExpired(currentSvc) ? 'warning' : 'success'}>
                        {isWcExpired(currentSvc) ? '已过期' : '运行中'}
                      </Tag>
                    )}
                    <Button
                      size="small"
                      type={currentSvc.wechat_channel?.enabled ? 'default' : 'primary'}
                      danger={!!currentSvc.wechat_channel?.enabled}
                      onClick={handleToggleWeChat}
                      style={
                        !currentSvc.wechat_channel?.enabled
                          ? { background: C.secondary, borderColor: C.secondary, color: C.text }
                          : undefined
                      }
                    >
                      {currentSvc.wechat_channel?.enabled ? '禁用' : '启用'}
                    </Button>
                  </Space>
                }
              >
                {renderWeChatContent()}
              </ModuleCard>

              {/* Module: 使用情况 — consumer 会话历史 + API 调用记录 */}
              <ModuleCard
                title="使用情况"
                icon={<ChartBar size={16} />}
                extra={
                  <Space size={8}>
                    <Segmented
                      size="small"
                      value={usageView}
                      onChange={(v) => setUsageView(v as 'convs' | 'records')}
                      options={[
                        { value: 'convs', label: `会话 (${svcConvs.length})` },
                        { value: 'records', label: `调用 (${svcUsage.length})` },
                      ]}
                    />
                    <Button
                      size="small"
                      icon={<ArrowsClockwise size={14} />}
                      onClick={() => {
                        if (!currentSvc) return;
                        if (usageView === 'convs') loadSvcConvs(currentSvc.id);
                        else loadSvcUsage(currentSvc.id, usageChannelFilter);
                      }}
                    >
                      刷新
                    </Button>
                  </Space>
                }
              >
                {usageView === 'convs' ? (
                  <Table
                    dataSource={svcConvs}
                    columns={svcConvColumns}
                    rowKey="id"
                    size="small"
                    loading={svcConvsLoading}
                    pagination={{ pageSize: 20, hideOnSinglePage: true, size: 'small' }}
                    locale={{ emptyText: '暂无 consumer 会话' }}
                    onRow={(_, index) => ({
                      style: { background: (index ?? 0) % 2 === 0 ? C.bg1 : C.bg2 },
                    })}
                  />
                ) : (
                  <>
                    <div style={{ marginBottom: 8 }}>
                      <Segmented
                        size="small"
                        value={usageChannelFilter}
                        onChange={(v) => {
                          const ch = v as '' | 'web' | 'api' | 'wechat';
                          setUsageChannelFilter(ch);
                          if (currentSvc) loadSvcUsage(currentSvc.id, ch);
                        }}
                        options={[
                          { value: '', label: '全部' },
                          { value: 'web', label: '网页' },
                          { value: 'api', label: 'API' },
                          { value: 'wechat', label: '微信' },
                        ]}
                      />
                    </div>
                    <Table
                      dataSource={svcUsage}
                      columns={svcUsageColumns}
                      rowKey={(r, idx) => `${r.ts}-${idx ?? 0}`}
                      size="small"
                      loading={svcUsageLoading}
                      pagination={{ pageSize: 50, hideOnSinglePage: true, size: 'small' }}
                      locale={{ emptyText: '暂无调用记录' }}
                      onRow={(_, index) => ({
                        style: { background: (index ?? 0) % 2 === 0 ? C.bg1 : C.bg2 },
                      })}
                    />
                  </>
                )}
              </ModuleCard>

              {/* Module: Test / API Endpoints */}
              <ModuleCard title="API 端点 & 测试" icon={<ArrowSquareOut size={16} />}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div>
                    <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>OpenAI 兼容接口</div>
                    <CopyBox value={`${apiOrigin}/api/v1/chat/completions`} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>自定义 SSE 接口</div>
                    <CopyBox value={`${apiOrigin}/api/v1/chat`} />
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>独立聊天页面</div>
                    <CopyBox
                      value={`${apiOrigin}/s/${currentSvc.id}`}
                      extra={
                        <Button size="small" icon={<LinkSimple size={16} />} onClick={() => window.open(`${apiOrigin}/s/${currentSvc.id}`, '_blank')}>
                          打开
                        </Button>
                      }
                    />
                  </div>
                </div>
              </ModuleCard>
            </div>
          </>
        )}
      </div>

      {/* ── Create / Edit Service Modal ── */}
      <Modal
        title={editingId ? '编辑 Service' : '创建 Service'}
        open={svcModalOpen}
        onCancel={() => setSvcModalOpen(false)}
        onOk={handleSaveService}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={560}
        destroyOnClose
        styles={modalStyles}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}>
            <Input placeholder="我的智能客服" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea placeholder="简要描述 Service 的用途" rows={2} />
          </Form.Item>
          <Form.Item name="model" label="模型" rules={[{ required: true, message: '请选择模型' }]}>
            <Select placeholder="选择模型">
              {models.map(m => (
                <Select.Option key={m.id} value={m.id}>{m.name}</Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            name="system_prompt_version_id"
            label="System Prompt 版本"
            tooltip="选择一个已保存的 Prompt 版本；留空则使用当前活跃版本"
          >
            <Select allowClear placeholder="使用当前 System Prompt">
              {promptVersions.map(v => (
                <Select.Option key={v.id} value={v.id}>
                  {v.label} ({fmtUserTime(v.timestamp, 'date')})
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            name="user_profile_version_id"
            label="User Profile 版本"
            tooltip="选择一个已保存的 Profile 版本；留空则使用当前活跃版本"
          >
            <Select allowClear placeholder="使用当前 User Profile">
              {profileVersions.map(v => (
                <Select.Option key={v.id} value={v.id}>
                  {v.label} ({fmtUserTime(v.timestamp, 'date')})
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            name="allowed_docs"
            label="允许的文档"
            tooltip="点击打开文件树勾选；'全部 (*)' 表示不限制"
          >
            <PickerField onClick={() => setDocPickerOpen(true)} placeholder="点击选择允许的文档…" />
          </Form.Item>
          <Form.Item
            name="allowed_scripts"
            label="允许的脚本"
            tooltip="点击打开文件树勾选；未选则 service 不可执行任何脚本"
          >
            <PickerField onClick={() => setScriptPickerOpen(true)} placeholder="点击选择允许的脚本（默认未选 = 禁止脚本）" />
          </Form.Item>
          <Form.Item name="capabilities" label="能力">
            <Checkbox.Group options={CAPABILITY_OPTIONS} />
          </Form.Item>

          <div style={{
            margin: '4px 0 16px',
            padding: '12px 14px',
            background: 'var(--jf-bg-deep)',
            borderRadius: 8,
            border: `1px solid ${C.border}`,
          }}>
            <div style={{ fontSize: 12, color: C.muted, marginBottom: 10, fontWeight: 600, letterSpacing: 0.4 }}>
              聊天页定制（独立链接 /s/&lt;id&gt;）
            </div>
            <Form.Item
              name="welcome_message"
              label="欢迎语"
              tooltip="在 chat 页首屏大字下方展示，发送第一条消息后自动隐藏。留空则不显示。"
              style={{ marginBottom: 12 }}
            >
              <TextArea placeholder="例如：你好！我是 XX 助手，可以帮你查阅产品资料、分析销售数据、生成报告。" rows={3} maxLength={300} showCount />
            </Form.Item>
            <Form.Item
              label="快速问题"
              tooltip="首屏展示的问题气泡；点击后立即作为用户消息发送。建议短而具体。"
              style={{ marginBottom: 0 }}
            >
              <Form.List name="quick_questions">
                {(fields, { add, remove }) => (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {fields.map((field) => (
                      <div key={field.key} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                        <Form.Item
                          name={field.name}
                          rules={[{ max: 80, message: '问题不超过 80 字' }]}
                          noStyle
                        >
                          <Input placeholder="例如：分析最近 7 天的销售趋势" maxLength={80} />
                        </Form.Item>
                        <Button
                          type="text"
                          danger
                          icon={<MinusCircleOutlined />}
                          onClick={() => remove(field.name)}
                        />
                      </div>
                    ))}
                    <Button
                      type="dashed"
                      block
                      icon={<PlusOutlined />}
                      onClick={() => add('')}
                      style={{ marginTop: 4 }}
                    >
                      添加快速问题
                    </Button>
                  </div>
                )}
              </Form.List>
            </Form.Item>
          </div>

          <Form.Item name="published" valuePropName="checked">
            <Checkbox>立即发布</Checkbox>
          </Form.Item>
        </Form>

        {/* 文件树选择器 */}
        <Form.Item noStyle shouldUpdate={(p, c) => p.allowed_docs !== c.allowed_docs}>
          {() => (
            <FileTreePicker
              open={docPickerOpen}
              title="选择允许的文档"
              rootPath="/docs"
              value={form.getFieldValue('allowed_docs') || []}
              enableAllShortcut
              emptyHint="未选 = service 无法访问任何文档"
              onCancel={() => setDocPickerOpen(false)}
              onOk={(next) => {
                form.setFieldValue('allowed_docs', next);
                setDocPickerOpen(false);
              }}
            />
          )}
        </Form.Item>
        <Form.Item noStyle shouldUpdate={(p, c) => p.allowed_scripts !== c.allowed_scripts}>
          {() => (
            <FileTreePicker
              open={scriptPickerOpen}
              title="选择允许的脚本"
              rootPath="/scripts"
              value={form.getFieldValue('allowed_scripts') || []}
              enableAllShortcut
              emptyHint="未选 = service 不可执行任何脚本（默认即如此）"
              onCancel={() => setScriptPickerOpen(false)}
              onOk={(next) => {
                form.setFieldValue('allowed_scripts', next);
                setScriptPickerOpen(false);
              }}
            />
          )}
        </Form.Item>
      </Modal>

      {/* ── Key Modal ── */}
      <Modal
        title="新 API Key"
        open={keyModalOpen}
        onCancel={() => {
          setKeyModalOpen(false);
          if (generatedKey && currentSvc) loadKeys(currentSvc.id);
        }}
        footer={null}
        width={440}
        destroyOnClose
        styles={modalStyles}
      >
        {!generatedKey ? (
          <div style={{ marginTop: 16 }}>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 12, color: C.muted, marginBottom: 4 }}>
                Key 名称
              </label>
              <Input
                value={keyName}
                onChange={e => setKeyName(e.target.value)}
                placeholder="default"
              />
            </div>
            <Button
              type="primary" block loading={keyGenerating} onClick={handleGenerateKey}
              style={{ background: C.primary, borderColor: C.primary }}
            >
              生成
            </Button>
          </div>
        ) : (
          <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Text style={{ fontSize: 13, color: C.warning }}>
              请立即复制，关闭后将无法再次查看完整值。
            </Text>
            <div>
              <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>API Key</div>
              <CopyBox value={generatedKey} />
            </div>
            {currentSvc && (
              <div>
                <div style={{ fontSize: 12, color: C.muted, marginBottom: 6 }}>
                  专属聊天链接（已附带 Key，分享即用）
                </div>
                <CopyBox
                  value={`${apiOrigin}/s/${currentSvc.id}?key=${encodeURIComponent(generatedKey)}`}
                  extra={
                    <Button
                      size="small"
                      icon={<LinkSimple size={16} />}
                      onClick={() => window.open(`${apiOrigin}/s/${currentSvc.id}?key=${encodeURIComponent(generatedKey)}`, '_blank')}
                    >
                      打开
                    </Button>
                  }
                />
                <div style={{ fontSize: 11, color: C.muted, marginTop: 6, lineHeight: 1.5 }}>
                  ⚠ 任何拿到此链接的人都能直接以该 Key 身份对话；URL 包含密钥，请勿放入公共渠道。打开后浏览器会自动从 URL 抹除 Key 并存入本地。
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* ── WeChat Chat History Modal ── */}
      <Modal
        title={chatTitle}
        open={chatModalOpen}
        onCancel={() => setChatModalOpen(false)}
        footer={null}
        width={600}
        destroyOnClose
        styles={modalStyles}
      >
        {chatLoading ? (
          <div style={{ padding: 32, textAlign: 'center' }}><Spin /></div>
        ) : chatMessages.length === 0 ? (
          <Empty description="暂无消息" />
        ) : (
          <div style={{ maxHeight: 400, overflowY: 'auto', padding: '16px 0' }}>
            {chatMessages.map((m, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                  marginBottom: 10,
                }}
              >
                <div style={{
                  maxWidth: '80%', padding: '10px 14px', borderRadius: 'var(--jf-radius-md)',
                  fontSize: 13, lineHeight: 1.55, wordBreak: 'break-word', whiteSpace: 'pre-wrap',
                  background: m.role === 'user' ? C.secondary : C.bg2,
                  color: m.role === 'user' ? C.text : C.text,
                  border: m.role === 'user' ? 'none' : `1px solid ${C.border}`,
                  boxShadow: m.role === 'user' ? FLOAT_SHADOW : undefined,
                }}
                >
                  {m.content}
                  {m.timestamp && (
                    <span style={{
                      display: 'block', fontSize: 10, opacity: 0.65, marginTop: 4,
                      color: m.role === 'user' ? C.bg0 : C.muted,
                      textAlign: m.role === 'user' ? 'right' : 'left',
                    }}
                    >
                      {fmtUserTime(m.timestamp, 'time')}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Modal>

      {/* ── Service Consumer 会话查看 Drawer ── */}
      <Drawer
        open={convDrawerOpen}
        onClose={() => setConvDrawerOpen(false)}
        width={720}
        destroyOnClose
        title={
          convDrawerData ? (
            <Space size={8}>
              <Tag color={SOURCE_COLORS[convDrawerData.source] ?? 'default'}>
                {SOURCE_LABELS[convDrawerData.source] ?? convDrawerData.source}
              </Tag>
              <span>{convDrawerData.title || `(无标题 · ${convDrawerData.id.slice(0, 8)})`}</span>
              <Text style={{ color: C.muted, fontSize: 12 }}>
                · {convDrawerData.message_count} 条
              </Text>
            </Space>
          ) : (
            '会话详情'
          )
        }
      >
        {convDrawerLoading ? (
          <div style={{ padding: 32, textAlign: 'center' }}><Spin /></div>
        ) : !convDrawerData || convDrawerData.messages.length === 0 ? (
          <Empty description="暂无消息" />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {convDrawerData.messages.map((m, i) => (
              <div
                key={i}
                style={{
                  borderLeft: `3px solid ${m.role === 'user' ? C.primary : C.secondary}`,
                  padding: '8px 12px',
                  background: C.bg2,
                  borderRadius: 4,
                }}
              >
                <div style={{ fontSize: 11, color: C.muted, marginBottom: 4 }}>
                  <strong style={{ color: C.text }}>{m.role}</strong>
                  {m.timestamp ? ` · ${fmtUserTime(m.timestamp, 'short')}` : ''}
                </div>
                <div style={{
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  fontSize: 13, lineHeight: 1.6,
                }}
                >
                  {typeof m.content === 'string' ? m.content : JSON.stringify(m.content)}
                </div>
                {m.tool_calls != null && (
                  <div style={{
                    fontSize: 11, color: C.muted, marginTop: 6,
                    fontFamily: "'Cascadia Code',monospace",
                  }}
                  >
                    tool_calls: {JSON.stringify(m.tool_calls).slice(0, 300)}
                    {JSON.stringify(m.tool_calls).length > 300 ? '…' : ''}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  );
}

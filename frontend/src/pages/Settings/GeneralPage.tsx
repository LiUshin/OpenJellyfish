import { useState, useEffect, useRef } from 'react';
import { Typography, Select, Spin, Tag, Switch, Input, Button, message, Collapse, Tooltip } from 'antd';
import { Clock, PaintBrush, Sliders, Lightning, Key, Eye, EyeSlash, CheckCircle, XCircle, ArrowsClockwise } from '@phosphor-icons/react';
import BatchRunner from '../../components/modals/BatchRunner';
import * as api from '../../services/api';
import { getTzOffset, setTzOffset, tzLabel, fmtUserTime } from '../../utils/timezone';
import { useTheme, type UiStyle } from '../../stores/themeContext';
import LogoLoading from '../../components/LogoLoading';

const { Text } = Typography;

const C = {
  bg0: 'var(--jf-bg-deep)',
  bg1: 'var(--jf-bg-panel)',
  bg2: 'var(--jf-bg-raised)',
  text: 'var(--jf-text)',
  muted: 'var(--jf-text-muted)',
  primary: 'var(--jf-accent)',
  border: 'var(--jf-border)',
};

const TZ_OPTIONS = [
  { value: -12, label: 'UTC-12 (Baker Island)' },
  { value: -11, label: 'UTC-11 (Samoa)' },
  { value: -10, label: 'UTC-10 (Hawaii)' },
  { value: -9, label: 'UTC-9 (Alaska)' },
  { value: -8, label: 'UTC-8 (Pacific)' },
  { value: -7, label: 'UTC-7 (Mountain)' },
  { value: -6, label: 'UTC-6 (Central)' },
  { value: -5, label: 'UTC-5 (Eastern)' },
  { value: -4, label: 'UTC-4 (Atlantic)' },
  { value: -3, label: 'UTC-3 (Buenos Aires)' },
  { value: -2, label: 'UTC-2' },
  { value: -1, label: 'UTC-1 (Azores)' },
  { value: 0, label: 'UTC+0 (London)' },
  { value: 1, label: 'UTC+1 (Paris)' },
  { value: 2, label: 'UTC+2 (Cairo)' },
  { value: 3, label: 'UTC+3 (Moscow)' },
  { value: 3.5, label: 'UTC+3:30 (Tehran)' },
  { value: 4, label: 'UTC+4 (Dubai)' },
  { value: 4.5, label: 'UTC+4:30 (Kabul)' },
  { value: 5, label: 'UTC+5 (Karachi)' },
  { value: 5.5, label: 'UTC+5:30 (Mumbai)' },
  { value: 5.75, label: 'UTC+5:45 (Kathmandu)' },
  { value: 6, label: 'UTC+6 (Dhaka)' },
  { value: 7, label: 'UTC+7 (Bangkok)' },
  { value: 8, label: 'UTC+8 (Beijing/Singapore)' },
  { value: 9, label: 'UTC+9 (Tokyo)' },
  { value: 9.5, label: 'UTC+9:30 (Adelaide)' },
  { value: 10, label: 'UTC+10 (Sydney)' },
  { value: 11, label: 'UTC+11 (Solomon)' },
  { value: 12, label: 'UTC+12 (Auckland)' },
  { value: 13, label: 'UTC+13 (Tonga)' },
  { value: 14, label: 'UTC+14 (Kiribati)' },
];

const STYLE_OPTIONS: { value: UiStyle; label: string }[] = [
  { value: 'regular', label: '默认样式 (Regular)' },
  { value: 'terminal', label: '终端样式 (Terminal)' },
];

const ADV_SYSTEM_KEY = 'show_advanced_system';
const ADV_SOUL_KEY = 'show_advanced_soul';

function getAdvFlag(key: string): boolean {
  return localStorage.getItem(key) === '1';
}

function setAdvFlag(key: string, val: boolean) {
  localStorage.setItem(key, val ? '1' : '0');
  window.dispatchEvent(new Event('advanced-settings-changed'));
}

interface KeyField {
  field: string;
  label: string;
  placeholder: string;
  helpUrl?: string;
  helpText?: string;
  isUrl?: boolean;
}

const KEY_SECTIONS: { title: string; fields: KeyField[] }[] = [
  {
    title: 'Anthropic (Claude)',
    fields: [
      {
        field: 'anthropic_api_key',
        label: 'API Key',
        placeholder: 'sk-ant-...',
        helpUrl: 'https://console.anthropic.com/settings/keys',
        helpText: '获取 Key',
      },
      { field: 'anthropic_base_url', label: 'Base URL (可选)', placeholder: '默认 https://api.anthropic.com', isUrl: true },
    ],
  },
  {
    title: 'OpenAI',
    fields: [
      {
        field: 'openai_api_key',
        label: 'API Key',
        placeholder: 'sk-...',
        helpUrl: 'https://platform.openai.com/api-keys',
        helpText: '获取 Key',
      },
      { field: 'openai_base_url', label: 'Base URL (可选)', placeholder: '默认 https://api.openai.com/v1', isUrl: true },
    ],
  },
  {
    title: '搜索 (Tavily)',
    fields: [
      {
        field: 'tavily_api_key',
        label: 'API Key',
        placeholder: 'tvly-...',
        helpUrl: 'https://tavily.com/#api',
        helpText: '获取 Key',
      },
    ],
  },
];

function StatusDot({ ok }: { ok: boolean | null }) {
  if (ok === null) return null;
  return ok
    ? <CheckCircle size={16} weight="fill" color="var(--jf-success)" />
    : <XCircle size={16} weight="fill" color="var(--jf-error)" />;
}

function ApiKeysCard() {
  const [masked, setMasked] = useState<api.ApiKeysMasked | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [showRaw, setShowRaw] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; error?: string }>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getApiKeys().then(k => { setMasked(k); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  const handleChange = (field: string, value: string) => {
    setEdits(prev => ({ ...prev, [field]: value }));
  };

  const handleSave = async () => {
    const updates: Record<string, string> = {};
    for (const [k, v] of Object.entries(edits)) {
      if (v !== undefined) updates[k] = v;
    }
    if (!Object.keys(updates).length) return;
    setSaving(true);
    try {
      const res = await api.updateApiKeys(updates);
      setMasked(res.keys);
      setEdits({});
      message.success('API Keys 已保存');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '保存失败');
    }
    setSaving(false);
  };

  const handleTest = async (provider: string) => {
    setTesting(provider);
    try {
      const res = await api.testApiKeys(provider);
      setTestResults(prev => ({ ...prev, ...res.results }));
    } catch {
      setTestResults(prev => ({ ...prev, [provider]: { ok: false, error: '测试请求失败' } }));
    }
    setTesting(null);
  };

  const handleTestAll = async () => {
    setTesting('all');
    try {
      const res = await api.testApiKeys('all');
      setTestResults(res.results);
    } catch {
      /* ignore */
    }
    setTesting(null);
  };

  if (loading) return <Spin size="small" />;

  const hasEdits = Object.keys(edits).length > 0;
  const providerMap: Record<string, string> = {
    'Anthropic (Claude)': 'anthropic',
    'OpenAI': 'openai',
    '搜索 (Tavily)': 'tavily',
  };

  return (
    <div style={{
      background: C.bg2,
      borderRadius: 'var(--jf-radius-lg)',
      border: `1px solid ${C.border}`,
      padding: '20px 24px',
      marginBottom: 16,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Key size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>API Keys</Text>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Tooltip title="测试所有连接">
            <Button
              size="small"
              icon={<ArrowsClockwise size={14} />}
              onClick={handleTestAll}
              loading={testing === 'all'}
            >
              全部测试
            </Button>
          </Tooltip>
          {hasEdits && (
            <Button
              type="primary"
              size="small"
              onClick={handleSave}
              loading={saving}
            >
              保存
            </Button>
          )}
        </div>
      </div>

      <Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 16 }}>
        至少配置 Claude 或 OpenAI 的 Key 才能使用 Agent。未填写 OpenAI Key 将无法使用图片/视频/语音生成。
        Key 加密存储在服务端，每个 Admin 及其所有 Agent 独立使用。
      </Text>

      <Collapse
        ghost
        defaultActiveKey={['Anthropic (Claude)', 'OpenAI', '搜索 (Tavily)']}
        style={{ background: 'transparent' }}
        items={KEY_SECTIONS.map(section => {
          const prov = providerMap[section.title] || '';
          const testRes = testResults[prov];
          return {
            key: section.title,
            label: (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Text style={{ color: C.text, fontSize: 13, fontWeight: 500 }}>{section.title}</Text>
                <StatusDot ok={testRes ? testRes.ok : null} />
                {testRes && !testRes.ok && testRes.error && (
                  <Text style={{ color: 'var(--jf-error)', fontSize: 11 }}>{testRes.error}</Text>
                )}
              </div>
            ),
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 8 }}>
                {section.fields.map(f => {
                  const isSecret = !f.isUrl;
                  const configured = masked?.[`${f.field}_configured`] as boolean | undefined;
                  const maskedVal = (masked?.[f.field] as string) || '';
                  const editVal = edits[f.field];
                  const isEditing = editVal !== undefined;

                  return (
                    <div key={f.field} style={{ display: 'grid', gridTemplateColumns: '140px 1fr auto', gap: 8, alignItems: 'center' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <Text style={{ color: C.muted, fontSize: 12 }}>{f.label}</Text>
                        {f.helpUrl && (
                          <a
                            href={f.helpUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ color: C.primary, fontSize: 11, textDecoration: 'none' }}
                          >
                            {f.helpText}
                          </a>
                        )}
                      </div>

                      <div style={{ position: 'relative' }}>
                        {isSecret ? (
                          <Input.Password
                            size="small"
                            placeholder={f.placeholder}
                            value={isEditing ? editVal : (configured ? maskedVal : '')}
                            onChange={e => handleChange(f.field, e.target.value)}
                            onFocus={() => {
                              if (!isEditing && configured) {
                                handleChange(f.field, '');
                              }
                            }}
                            visibilityToggle={{
                              visible: showRaw[f.field] || false,
                              onVisibleChange: (v) => setShowRaw(prev => ({ ...prev, [f.field]: v })),
                            }}
                            iconRender={visible =>
                              visible
                                ? <Eye size={14} style={{ cursor: 'pointer' }} />
                                : <EyeSlash size={14} style={{ cursor: 'pointer' }} />
                            }
                            style={{ fontFamily: "'Cascadia Code', monospace", fontSize: 12 }}
                          />
                        ) : (
                          <Input
                            size="small"
                            placeholder={f.placeholder}
                            value={isEditing ? editVal : maskedVal}
                            onChange={e => handleChange(f.field, e.target.value)}
                            style={{ fontFamily: "'Cascadia Code', monospace", fontSize: 12 }}
                          />
                        )}
                      </div>

                      <div style={{ width: 20 }}>
                        {isSecret && configured && !isEditing && (
                          <CheckCircle size={14} weight="fill" color="var(--jf-success)" />
                        )}
                      </div>
                    </div>
                  );
                })}

                {prov && (
                  <div style={{ textAlign: 'right' }}>
                    <Button
                      size="small"
                      type="link"
                      onClick={() => handleTest(prov)}
                      loading={testing === prov}
                      style={{ fontSize: 12, padding: 0 }}
                    >
                      测试连接
                    </Button>
                  </div>
                )}
              </div>
            ),
          };
        })}
      />
    </div>
  );
}

export default function GeneralPage() {
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(getTzOffset());
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [tick, setTick] = useState(0);
  const { uiStyle, setUiStyle } = useTheme();
  const [showSystem, setShowSystem] = useState(getAdvFlag(ADV_SYSTEM_KEY));
  const [showSoul, setShowSoul] = useState(getAdvFlag(ADV_SOUL_KEY));

  useEffect(() => {
    api.getPreferences().then((prefs) => {
      const tz = prefs.tz_offset_hours ?? 8;
      setOffset(tz);
      setTzOffset(tz);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    timerRef.current = setInterval(() => setTick(t => t + 1), 1000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, []);

  const handleTzChange = async (val: number) => {
    setOffset(val);
    setTzOffset(val);
    setSaving(true);
    try {
      await api.updatePreferences({ tz_offset_hours: val });
    } catch { /* ignore */ }
    setSaving(false);
  };

  if (loading) {
    return <LogoLoading size={240} />;
  }

  void tick;
  const nowIso = new Date().toISOString();
  const serverUtcStr = nowIso.replace('T', ' ').slice(0, 19);
  const userTimeStr = fmtUserTime(nowIso, 'datetime');

  return (
    <div style={{ padding: '24px 32px', maxWidth: 960, margin: '0 auto', width: '100%' }}>
      <Text style={{ color: C.text, fontSize: 18, fontWeight: 600, display: 'block', marginBottom: 20 }}>
        通用设置
      </Text>

      {/* API Keys */}
      <ApiKeysCard />

      {/* Time & Timezone */}
      <div style={{
        background: C.bg2,
        borderRadius: 'var(--jf-radius-lg)',
        border: `1px solid ${C.border}`,
        padding: '20px 24px',
        marginBottom: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <Clock size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>时间与时区</Text>
          {saving && <Spin size="small" style={{ marginLeft: 8 }} />}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '100px 1fr', gap: '12px 16px', alignItems: 'center' }}>
          <Text style={{ color: C.muted, fontSize: 13 }}>时区偏移</Text>
          <Select
            value={offset}
            onChange={handleTzChange}
            style={{ maxWidth: 360, width: '100%' }}
            options={TZ_OPTIONS}
            showSearch
            optionFilterProp="label"
          />

          <Text style={{ color: C.muted, fontSize: 13 }}>服务器 UTC</Text>
          <div>
            <Tag color="blue" style={{ fontFamily: "'Cascadia Code', monospace", fontSize: 13 }}>
              {serverUtcStr}
            </Tag>
          </div>

          <Text style={{ color: C.muted, fontSize: 13 }}>用户时间</Text>
          <div>
            <Tag color="green" style={{ fontFamily: "'Cascadia Code', monospace", fontSize: 13 }}>
              {userTimeStr}
            </Tag>
            <Text style={{ color: C.muted, fontSize: 11, marginLeft: 4 }}>
              {tzLabel(offset)}
            </Text>
          </div>
        </div>

        <div style={{ marginTop: 14 }}>
          <Text style={{ color: C.muted, fontSize: 12 }}>
            此设置影响：系统中所有时间的显示、Agent Prompt 中的当前时间、定时任务的时间展示等。
          </Text>
        </div>
      </div>

      {/* UI Style */}
      <div style={{
        background: C.bg2,
        borderRadius: 'var(--jf-radius-lg)',
        border: `1px solid ${C.border}`,
        padding: '20px 24px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <PaintBrush size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>界面样式</Text>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '100px 1fr', gap: '12px 16px', alignItems: 'center' }}>
          <Text style={{ color: C.muted, fontSize: 13 }}>样式风格</Text>
          <Select
            value={uiStyle}
            onChange={(v: UiStyle) => setUiStyle(v)}
            style={{ maxWidth: 360, width: '100%' }}
            options={STYLE_OPTIONS}
          />
        </div>

        <div style={{ marginTop: 14 }}>
          <Text style={{ color: C.muted, fontSize: 12 }}>
            终端样式会将界面切换为 monospace 字体、直角边框和 CRT 扫描线效果。明暗色可通过左下角的切换按钮调整。
          </Text>
        </div>
      </div>

      {/* Batch run */}
      <div style={{
        background: C.bg2,
        borderRadius: 'var(--jf-radius-lg)',
        border: `1px solid ${C.border}`,
        padding: '20px 24px',
        marginTop: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <Lightning size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>批量运行</Text>
        </div>
        <Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 16 }}>
          上传 Excel，按列配置批量调用 Agent，可在下方查看进度与下载结果。
        </Text>
        <BatchRunner open onClose={() => {}} inline />
      </div>

      {/* Advanced Pages */}
      <div style={{
        background: C.bg2,
        borderRadius: 'var(--jf-radius-lg)',
        border: `1px solid ${C.border}`,
        padding: '20px 24px',
        marginTop: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <Sliders size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>高级功能</Text>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <Text style={{ color: C.text, fontSize: 13 }}>操作规则</Text>
              <Tag color="purple" style={{ marginLeft: 6, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>Advanced</Tag>
              <div>
                <Text style={{ color: C.muted, fontSize: 12 }}>
                  在 Prompt 设置中显示「操作规则」Tab，可自定义 Agent 的 System Prompt 和能力提示词。
                </Text>
              </div>
            </div>
            <Switch
              checked={showSystem}
              onChange={(v) => { setShowSystem(v); setAdvFlag(ADV_SYSTEM_KEY, v); }}
              style={{ flexShrink: 0 }}
            />
          </div>

          <div style={{ height: 1, background: C.border }} />

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <Text style={{ color: C.text, fontSize: 13 }}>Memory & Soul</Text>
              <Tag color="purple" style={{ marginLeft: 6, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>Advanced</Tag>
              <div>
                <Text style={{ color: C.muted, fontSize: 12 }}>
                  在 Prompt 设置中显示「Memory & Soul」Tab，可配置记忆系统和 Soul 文件系统。
                </Text>
              </div>
            </div>
            <Switch
              checked={showSoul}
              onChange={(v) => { setShowSoul(v); setAdvFlag(ADV_SOUL_KEY, v); }}
              style={{ flexShrink: 0 }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

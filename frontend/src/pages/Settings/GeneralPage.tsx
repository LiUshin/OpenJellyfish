import SettingsSectionNav from '../../components/SettingsSectionNav';
import SettingsLoadError from '../../components/SettingsLoadError';
import { useState, useEffect, useRef } from 'react';
import { Typography, Select, Spin, Tag, Switch, Input, InputNumber, Button, message, Tabs, Tooltip, Divider, Segmented } from 'antd';
import { Clock, PaintBrush, Sliders, Lightning, Key, Eye, EyeSlash, CheckCircle, XCircle, ArrowsClockwise, Translate, Faders, MagnifyingGlass } from '@phosphor-icons/react';
import type { ModelVisibilityItem } from '../../types';
import { useTranslation } from 'react-i18next';
import BatchRunner from '../../components/modals/BatchRunner';
import LanguageSwitcher from '../../components/LanguageSwitcher';
import * as api from '../../services/api';
import { getTzOffset, setTzOffset, tzLabel, fmtUserTime } from '../../utils/timezone';
import { useTheme, type UiStyle } from '../../stores/themeContext';
import { getYoloMode, setYoloMode, YOLO_EVENT } from '../../utils/yoloMode';
import LogoLoading from '../../components/LogoLoading';
import RuntimeSettingsCard from '../../components/RuntimeSettingsCard';
import { useIsMobile } from '../../hooks/useMediaQuery';

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

const STYLE_OPTIONS: { value: UiStyle; labelKey: string }[] = [
  { value: 'regular', labelKey: 'general.uiStyleRegular' },
  { value: 'terminal', labelKey: 'general.uiStyleTerminal' },
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

// KEY_SECTIONS lives inside ApiKeysCard now (needs t() for i18n).

function StatusDot({ ok }: { ok: boolean | null }) {
  if (ok === null) return null;
  return ok
    ? <CheckCircle size={16} weight="fill" color="var(--jf-success)" />
    : <XCircle size={16} weight="fill" color="var(--jf-error)" />;
}

type CredSource = 'platform' | 'user';

function ApiKeysCard() {
  const isMobile = useIsMobile();
  const { t } = useTranslation();
  const [masked, setMasked] = useState<api.ApiKeysMasked | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [sources, setSources] = useState<Record<string, CredSource>>({});
  const [sourcesDirty, setSourcesDirty] = useState(false);
  const [showRaw, setShowRaw] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; error?: string }>>({});
  const [loadError, setLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [loading, setLoading] = useState(true);

  // Localised KEY_SECTIONS — labels/help text/placeholder swap with language.
  // Section titles are kept stable for `defaultActiveKey` matching; the Tavily
  // section's display title comes from i18n via `displayTitleKey`.
  const KEY_SECTIONS_I18N: { title: string; displayTitleKey?: string; fields: KeyField[] }[] = [
    {
      title: 'Anthropic (Claude)',
      fields: [
        { field: 'anthropic_api_key', label: 'API Key', placeholder: 'sk-ant-...',
          helpUrl: 'https://console.anthropic.com/settings/keys', helpText: t('general.apiKeysHelpText') },
        { field: 'anthropic_base_url', label: t('general.apiKeysBaseUrlOpt'),
          placeholder: t('general.apiKeysBaseUrlAnthropic'), isUrl: true },
      ],
    },
    {
      title: 'OpenAI',
      fields: [
        { field: 'openai_api_key', label: 'API Key', placeholder: 'sk-...',
          helpUrl: 'https://platform.openai.com/api-keys', helpText: t('general.apiKeysHelpText') },
        { field: 'openai_base_url', label: t('general.apiKeysBaseUrlOpt'),
          placeholder: t('general.apiKeysBaseUrlOpenAI'), isUrl: true },
      ],
    },
    {
      title: 'Kimi (Moonshot)',
      fields: [
        { field: 'kimi_api_key', label: 'API Key', placeholder: 'sk-...',
          helpUrl: 'https://platform.moonshot.cn/console/api-keys', helpText: t('general.apiKeysHelpText') },
        { field: 'kimi_base_url', label: t('general.apiKeysBaseUrlOpt'),
          placeholder: t('general.apiKeysBaseUrlKimi'), isUrl: true },
      ],
    },
    {
      title: 'MiniMax（语音/视频/对话）',
      displayTitleKey: 'general.apiKeysMinimax',
      fields: [
        { field: 'minimax_api_key', label: 'API Key', placeholder: 'eyJ... (Bearer)',
          helpUrl: 'https://platform.minimax.io/user-center/basic-information/interface-key',
          helpText: t('general.apiKeysHelpText') },
        { field: 'minimax_group_id', label: 'Group ID',
          placeholder: t('general.apiKeysGroupIdPh'),
          helpUrl: 'https://platform.minimax.io/user-center/basic-information',
          helpText: t('general.apiKeysHelpGroupId') },
      ],
    },
    {
      title: 'AWS Bedrock',
      fields: [
        { field: 'bedrock_api_key', label: 'API Key', placeholder: 'ABSK... (Bearer Token)',
          helpUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html',
          helpText: t('general.apiKeysHelpText') },
        { field: 'bedrock_region', label: 'Region',
          placeholder: 'us-east-1' },
      ],
    },
    {
      title: 'OpenRouter',
      fields: [
        { field: 'openrouter_api_key', label: 'API Key', placeholder: 'sk-or-...',
          helpUrl: 'https://openrouter.ai/keys', helpText: t('general.apiKeysHelpText') },
        { field: 'openrouter_base_url', label: t('general.apiKeysBaseUrlOpt'),
          placeholder: t('general.apiKeysBaseUrlOpenRouter'), isUrl: true },
      ],
    },
    {
      title: '硅基流动 (SiliconFlow)',
      fields: [
        { field: 'siliconflow_api_key', label: 'API Key', placeholder: 'sk-...',
          helpUrl: 'https://cloud.siliconflow.cn/account/ak', helpText: t('general.apiKeysHelpText') },
        { field: 'siliconflow_base_url', label: t('general.apiKeysBaseUrlOpt'),
          placeholder: t('general.apiKeysBaseUrlSiliconFlow'), isUrl: true },
      ],
    },
    {
      title: '搜索 (Tavily)',
      displayTitleKey: 'general.apiKeysSearchTavily',
      fields: [
        { field: 'tavily_api_key', label: 'API Key', placeholder: 'tvly-...',
          helpUrl: 'https://tavily.com/#api', helpText: t('general.apiKeysHelpText') },
      ],
    },
  ];

  useEffect(() => {
    let disposed = false;
    setLoading(true); setLoadError(false);
    api.getApiKeys().then(k => {
      if (disposed) return;
      setMasked(k);
      const src = (k.credential_sources || {}) as Record<string, CredSource>;
      setSources(src);
      setSourcesDirty(false);
      setLoading(false);
    }).catch(() => { if (!disposed) { setLoadError(true); setLoading(false); } });
    return () => { disposed = true; };
  }, [loadAttempt]);

  const handleChange = (field: string, value: string) => {
    setEdits(prev => ({ ...prev, [field]: value }));
  };

  const handleSourceChange = (provider: string, src: CredSource) => {
    setSources(prev => ({ ...prev, [provider]: src }));
    setSourcesDirty(true);
  };

  const handleSave = async () => {
    const updates: Record<string, string> = {};
    for (const [k, v] of Object.entries(edits)) {
      if (v !== undefined) updates[k] = v;
    }
    if (!Object.keys(updates).length && !sourcesDirty) return;
    setSaving(true);
    try {
      const res = await api.updateApiKeys({
        ...updates,
        ...(sourcesDirty ? { credential_sources: sources } : {}),
      });
      setMasked(res.keys);
      if (res.keys.credential_sources) {
        setSources(res.keys.credential_sources as Record<string, CredSource>);
      }
      setEdits({});
      setSourcesDirty(false);
      message.success(t('general.apiKeysSaved'));
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('common.saveFailed'));
    }
    setSaving(false);
  };

  const handleTest = async (provider: string) => {
    setTesting(provider);
    try {
      const res = await api.testApiKeys(provider);
      setTestResults(prev => ({ ...prev, ...res.results }));
    } catch {
      setTestResults(prev => ({ ...prev, [provider]: { ok: false, error: t('general.apiKeysTestRequestFailed') } }));
    }
    setTesting(null);
  };

  const handleTestAll = async () => {
    setTesting('all');
    try {
      const res = await api.testApiKeys('all');
      setTestResults(res.results);
    } catch {
      message.error(t('general.apiKeysTestRequestFailed'));
    }
    setTesting(null);
  };

  if (loading) return <Spin size="small" />;
  if (loadError) return <SettingsLoadError onRetry={() => setLoadAttempt(n => n + 1)} />;

  const hasEdits = Object.keys(edits).length > 0 || sourcesDirty;
  const providerMap: Record<string, string> = {
    'Anthropic (Claude)': 'anthropic',
    'OpenAI': 'openai',
    'Kimi (Moonshot)': 'kimi',
    'MiniMax（语音/视频/对话）': 'minimax',
    'AWS Bedrock': 'bedrock',
    'OpenRouter': 'openrouter',
    '硅基流动 (SiliconFlow)': 'siliconflow',
    '搜索 (Tavily)': 'tavily',
  };
  const platformConfigured = (masked?.platform_configured || {}) as Record<string, boolean>;

  return (
    <div className="credentials-card" style={{
      background: C.bg1,
      borderRadius: 'var(--jf-radius-lg)',
      border: `1px solid ${C.border}`,
      padding: isMobile ? '16px 14px' : '20px 24px',
      marginBottom: 16,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16, flexWrap: isMobile ? 'wrap' : 'nowrap', gap: isMobile ? 8 : 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Key size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>{t('general.apiKeysCard')}</Text>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Tooltip title={t('general.apiKeysTestAllTip')}>
            <Button
              size="small"
              icon={<ArrowsClockwise size={14} />}
              disabled={hasEdits || testing !== null}
              onClick={handleTestAll}
              loading={testing === 'all'}
            >
              {t('general.apiKeysTestAll')}
            </Button>
          </Tooltip>

        </div>
      </div>

      <Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 16 }}>
        {t('settingsDesign.credentialsHint')}
      </Text>

      <details className="settings-inline-help"><summary>{t('settingsDesign.credentialsDetails')}</summary><p>{t('general.apiKeysDesc')}</p></details>
      <Tabs
        className="provider-workspace"
        tabPosition={isMobile ? 'top' : 'left'}
        defaultActiveKey="OpenAI"
        style={{ background: 'transparent' }}
        items={KEY_SECTIONS_I18N.map(section => {
          const prov = providerMap[section.title] || '';
          const testRes = testResults[prov];
          const displayTitle = section.displayTitleKey ? t(section.displayTitleKey) : section.title;
          const activeSource: CredSource = (sources[prov] as CredSource) || 'platform';
          const platOk = !!platformConfigured[prov];
          return {
            key: section.title,
            label: (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <Text style={{ color: C.text, fontSize: 13, fontWeight: 500 }}>{displayTitle}</Text>
                {prov && (
                  <Tag
                    style={{ margin: 0, fontSize: 11, lineHeight: '18px' }}
                    color={activeSource === 'platform' ? 'cyan' : 'purple'}
                  >
                    {activeSource === 'platform'
                      ? t('general.apiKeysSourcePlatformTag')
                      : t('general.apiKeysSourceUserTag')}
                  </Tag>
                )}
                <StatusDot ok={testRes ? testRes.ok : null} />
                {testRes && !testRes.ok && testRes.error && (
                  <Text style={{ color: 'var(--jf-error)', fontSize: 11 }}>{testRes.error}</Text>
                )}
              </div>
            ),
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 8 }}>
                {prov && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                      <Text style={{ color: C.muted, fontSize: 12 }}>{t('general.apiKeysSourceLabel')}</Text>
                      <Segmented
                        size="small"
                        value={activeSource}
                        disabled={saving}
                        onChange={(v) => handleSourceChange(prov, v as CredSource)}
                        options={[
                          {
                            value: 'platform',
                            label: t('general.apiKeysSourcePlatform'),
                          },
                          {
                            value: 'user',
                            label: t('general.apiKeysSourceUser'),
                          },
                        ]}
                      />
                      {activeSource === 'platform' && (
                        <Text style={{ color: C.muted, fontSize: 11 }}>
                          {platOk
                            ? t('general.apiKeysPlatformReady')
                            : t('general.apiKeysPlatformMissing')}
                        </Text>
                      )}
                    </div>
                    <Text style={{ color: C.muted, fontSize: 11 }}>
                      {activeSource === 'platform'
                        ? t('general.apiKeysSourcePlatformHint')
                        : t('general.apiKeysSourceUserHint')}
                    </Text>
                  </div>
                )}
                {section.fields.map(f => {
                  const isSecret = !f.isUrl;
                  const configured = masked?.[`${f.field}_configured`] as boolean | undefined;
                  const maskedVal = (masked?.[f.field] as string) || '';
                  const editVal = edits[f.field];
                  const isEditing = editVal !== undefined;

                  return (
                    <div key={f.field} style={{
                      display: 'grid',
                      gridTemplateColumns: isMobile ? '1fr auto' : '140px 1fr auto',
                      gap: isMobile ? '4px 8px' : 8,
                      alignItems: 'center',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4, gridColumn: isMobile ? '1 / -1' : 'auto' }}>
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
                            aria-label={`${displayTitle} ${f.label}`}
                            disabled={saving}
                            placeholder={configured ? t('settingsDesign.replaceCredential') : f.placeholder}
                            value={isEditing ? editVal : ''}
                            onChange={e => handleChange(f.field, e.target.value)}
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
                            aria-label={`${displayTitle} ${f.label}`}
                            disabled={saving}
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
                      disabled={hasEdits || testing !== null}
                      onClick={() => handleTest(prov)}
                      loading={testing === prov}
                      style={{ fontSize: 12, padding: 0 }}
                    >
                      {t('general.apiKeysTest')}
                    </Button>
                  </div>
                )}
              </div>
            ),
          };
        })}
      />
      <div className="settings-savebar credentials-savebar">
        <span role="status">{t(hasEdits ? 'settingsPolish.unsaved' : 'settingsPolish.saved')}{hasEdits && <small>{t('settingsDesign.testSaved')}</small>}</span>
        <Button type="primary" disabled={!hasEdits} loading={saving} onClick={handleSave}>{t('common.save')}</Button>
      </div>
    </div>
  );
}

// ── Provider display name mapping ────────────────────────────────
const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic (Claude)',
  openai: 'OpenAI',
  kimi: 'Kimi (Moonshot)',
  minimax: 'MiniMax',
  bedrock: 'AWS Bedrock',
  openrouter: 'OpenRouter',
  siliconflow: '硅基流动 (SiliconFlow)',
};

/**
 * OpenRouter publishes per-model capabilities, so reasoning can be pre-checked
 * from the listing. SiliconFlow's /v1/models is the bare OpenAI shape with no
 * capability data, so its models start off and the Admin decides.
 */
function remoteSupportsReasoning(m: api.AggregatorRemoteModel): boolean {
  const params = m.supported_parameters;
  if (!Array.isArray(params)) return false;
  return params.some(p => p === 'reasoning' || p === 'include_reasoning');
}

const DEFAULT_THINKING_BUDGET = 4096;

function AggregatorModelsCard({
  provider,
  showThinkingBudget = false,
}: {
  provider: api.AggregatorProvider;
  showThinkingBudget?: boolean;
}) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [enabled, setEnabled] = useState<api.AggregatorEnabledModel[]>([]);
  const [remote, setRemote] = useState<api.AggregatorRemoteModel[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const vendor = PROVIDER_LABELS[provider] || provider;

  const loadAll = async () => {
    setLoading(true);
    try {
      const [wl, models] = await Promise.all([
        api.getAggregatorEnabledModels(provider),
        api.fetchAggregatorRemoteModels(provider),
      ]);
      setEnabled(wl.models || []);
      setRemote(models);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('general.aggregatorLoadFailed', { vendor }));
    }
    setLoading(false);
  };

  useEffect(() => { void loadAll(); }, [provider]);

  const enabledMap = enabled.reduce<Record<string, api.AggregatorEnabledModel>>((acc, m) => {
    acc[m.id] = m;
    return acc;
  }, {});

  const persist = async (next: api.AggregatorEnabledModel[]) => {
    setSaving(true);
    const prev = enabled;
    setEnabled(next);
    try {
      const res = await api.setAggregatorEnabledModels(provider, next);
      setEnabled(res.models || next);
      message.success(t('general.aggregatorSaved', { vendor }));
    } catch {
      setEnabled(prev);
      message.error(t('common.saveFailed'));
    }
    setSaving(false);
  };

  const handleToggle = (m: api.AggregatorRemoteModel, on: boolean) => {
    if (on) {
      const row: api.AggregatorEnabledModel = {
        id: m.id,
        name: m.name || m.id,
        reasoning: enabledMap[m.id]?.reasoning ?? remoteSupportsReasoning(m),
      };
      void persist([...enabled.filter(x => x.id !== m.id), row]);
    } else {
      void persist(enabled.filter(x => x.id !== m.id));
    }
  };

  const handleReasoning = (id: string, reasoning: boolean) => {
    if (!enabledMap[id]) return;
    void persist(enabled.map(m => (m.id === id ? { ...m, reasoning } : m)));
  };

  const handleBudget = (id: string, budget: number | null) => {
    if (!enabledMap[id]) return;
    void persist(enabled.map(m => {
      if (m.id !== id) return m;
      const next = { ...m };
      if (budget == null) delete next.thinking_budget;
      else next.thinking_budget = budget;
      return next;
    }));
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const models = await api.fetchAggregatorRemoteModels(provider);
      setRemote(models);
      message.success(t('general.aggregatorRefreshed', { count: models.length }));
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('general.aggregatorLoadFailed', { vendor }));
    }
    setRefreshing(false);
  };

  const q = query.trim().toLowerCase();
  const filtered = !q
    ? remote
    : remote.filter(m => {
        const hay = `${m.id} ${m.name || ''} ${m.description || ''}`.toLowerCase();
        return hay.includes(q);
      });

  // Prefer showing enabled models first in search results (stable slice for UX).
  const sorted = [...filtered].sort((a, b) => {
    const ae = enabledMap[a.id] ? 0 : 1;
    const be = enabledMap[b.id] ? 0 : 1;
    if (ae !== be) return ae - be;
    return (a.name || a.id).localeCompare(b.name || b.id);
  });
  const visible = sorted.slice(0, 80);

  const cardBase: React.CSSProperties = {
    background: C.bg1,
    borderRadius: 'var(--jf-radius-lg)',
    border: `1px solid ${C.border}`,
    padding: isMobile ? '16px 14px' : '20px 24px',
    marginBottom: 16,
  };

  return (
    <div style={cardBase}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, gap: 8, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <MagnifyingGlass size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>
            {t('general.aggregatorModelsTitle', { vendor })}
          </Text>
          {saving && <Spin size="small" />}
        </div>
        <Button
          size="small"
          icon={<ArrowsClockwise size={14} />}
          onClick={() => void handleRefresh()}
          loading={refreshing}
        >
          {t('general.aggregatorRefresh')}
        </Button>
      </div>
      <Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 12 }}>
        {t(`general.${provider}ModelsDesc`)}
      </Text>

      <Input
        size="small"
        allowClear
        prefix={<MagnifyingGlass size={14} color={C.muted} />}
        placeholder={t('general.aggregatorSearchPh')}
        value={query}
        onChange={e => setQuery(e.target.value)}
        style={{ marginBottom: 12 }}
      />

      {enabled.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Text style={{ color: C.muted, fontSize: 11, display: 'block', marginBottom: 6 }}>
            {t('general.aggregatorEnabledCount', { count: enabled.length })}
          </Text>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {enabled.map(m => (
              <Tag
                key={m.id}
                closable
                onClose={(e) => {
                  e.preventDefault();
                  void persist(enabled.filter(x => x.id !== m.id));
                }}
                color={m.reasoning ? 'purple' : 'default'}
                style={{ marginInlineEnd: 0 }}
              >
                {m.name || m.id}
              </Tag>
            ))}
          </div>
        </div>
      )}

      {loading ? (
        <Spin size="small" />
      ) : remote.length === 0 ? (
        <Text style={{ color: C.muted, fontSize: 13 }}>
          {t('general.aggregatorEmpty', { vendor })}
        </Text>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 360, overflowY: 'auto' }}>
          {visible.map(m => {
            const on = !!enabledMap[m.id];
            const reasoning = enabledMap[m.id]?.reasoning ?? remoteSupportsReasoning(m);
            return (
              <div
                key={m.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '6px 10px',
                  borderRadius: 'var(--jf-radius)',
                  opacity: on ? 1 : 0.7,
                  gap: 8,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <Text style={{ color: C.text, fontSize: 13 }}>{m.name || m.id}</Text>
                    {remoteSupportsReasoning(m) && (
                      <Tag color="orange" style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', marginInlineEnd: 0 }}>
                        reasoning
                      </Tag>
                    )}
                  </div>
                  <Text style={{ color: C.muted, fontSize: 11 }}>{m.id}</Text>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                  {on && showThinkingBudget && reasoning && (
                    <Tooltip title={t('general.aggregatorBudgetTip')}>
                      <InputNumber
                        size="small"
                        min={128}
                        max={32768}
                        step={512}
                        style={{ width: 88 }}
                        placeholder={String(DEFAULT_THINKING_BUDGET)}
                        value={enabledMap[m.id]?.thinking_budget ?? null}
                        onChange={(v) => handleBudget(m.id, typeof v === 'number' ? v : null)}
                      />
                    </Tooltip>
                  )}
                  {on && (
                    <Tooltip title={t(`general.${provider}ReasoningTip`)}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <Text style={{ color: C.muted, fontSize: 11 }}>{t('general.aggregatorReasoning')}</Text>
                        <Switch
                          size="small"
                          checked={reasoning}
                          onChange={(v) => handleReasoning(m.id, v)}
                        />
                      </div>
                    </Tooltip>
                  )}
                  <Switch
                    size="small"
                    checked={on}
                    onChange={(v) => handleToggle(m, v)}
                  />
                </div>
              </div>
            );
          })}
          {filtered.length > visible.length && (
            <Text style={{ color: C.muted, fontSize: 11, padding: '4px 10px' }}>
              {t('general.aggregatorTruncated', { shown: visible.length, total: filtered.length })}
            </Text>
          )}
        </div>
      )}
    </div>
  );
}

const TIER_COLORS: Record<string, string> = {
  thinking: 'purple',
  high: 'blue',
  fast: 'green',
  reasoning: 'orange',
};

function ModelVisibilityCard() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [models, setModels] = useState<ModelVisibilityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);

  useEffect(() => {
    api.getModelVisibility()
      .then(res => setModels(res.models))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleToggle = async (modelId: string, enabled: boolean) => {
    setToggling(modelId);
    // Optimistic update
    setModels(prev => prev.map(m => m.id === modelId ? { ...m, enabled } : m));
    try {
      await api.toggleModelVisibility(modelId, enabled);
    } catch {
      // Revert on error
      setModels(prev => prev.map(m => m.id === modelId ? { ...m, enabled: !enabled } : m));
      message.error(t('common.saveFailed'));
    }
    setToggling(null);
  };

  // Group by provider
  const grouped = models.reduce<Record<string, ModelVisibilityItem[]>>((acc, m) => {
    const prov = m.provider || 'other';
    if (!acc[prov]) acc[prov] = [];
    acc[prov].push(m);
    return acc;
  }, {});

  const cardBase: React.CSSProperties = {
    background: C.bg1,
    borderRadius: 'var(--jf-radius-lg)',
    border: `1px solid ${C.border}`,
    padding: isMobile ? '16px 14px' : '20px 24px',
    marginBottom: 16,
  };

  return (
    <div style={cardBase}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <Faders size={18} color={C.primary} />
        <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>
          {t('general.modelVisibilityTitle', '可用模型')}
        </Text>
      </div>
      <Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 16 }}>
        {t('general.modelVisibilityDesc', '打开的模型才会出现在对话框的模型选择列表中。')}
      </Text>

      {loading ? (
        <Spin size="small" />
      ) : models.length === 0 ? (
        <Text style={{ color: C.muted, fontSize: 13 }}>
          {t('general.modelVisibilityEmpty', '未检测到可用模型，请先在上方配置 API Key。')}
        </Text>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {Object.entries(grouped).map(([provider, items], gi) => (
            <div key={provider}>
              {gi > 0 && <Divider style={{ margin: '12px 0', borderColor: C.border }} />}
              <Text style={{ color: C.muted, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 8 }}>
                {PROVIDER_LABELS[provider] || provider}
              </Text>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {items.map(m => (
                  <div
                    key={m.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '6px 10px',
                      borderRadius: 'var(--jf-radius)',
                      background: m.enabled ? 'transparent' : 'transparent',
                      opacity: m.enabled ? 1 : 0.45,
                      gap: 8,
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <Text style={{ color: C.text, fontSize: 13 }}>{m.name}</Text>
                        {m.tier && (
                          <Tag
                            color={TIER_COLORS[m.tier] || 'default'}
                            style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', marginInlineEnd: 0 }}
                          >
                            {m.tier}
                          </Tag>
                        )}
                      </div>
                      <Text style={{ color: C.muted, fontSize: 11 }}>{m.id}</Text>
                    </div>
                    <Switch
                      size="small"
                      checked={m.enabled}
                      loading={toggling === m.id}
                      onChange={(checked) => handleToggle(m.id, checked)}
                      style={{ flexShrink: 0 }}
                    />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function GeneralPage() {
  const isMobile = useIsMobile();
  const { t } = useTranslation();
  const [section, setSection] = useState('engine');
  const [loadError, setLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(getTzOffset());
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [tick, setTick] = useState(0);
  const { uiStyle, setUiStyle } = useTheme();
  const [showSystem, setShowSystem] = useState(getAdvFlag(ADV_SYSTEM_KEY));
  const [showSoul, setShowSoul] = useState(getAdvFlag(ADV_SOUL_KEY));
  const [yolo, setYolo] = useState(getYoloMode());

  const styleOptions = STYLE_OPTIONS.map(o => ({ value: o.value, label: t(o.labelKey) }));

  useEffect(() => {
    const sync = () => setYolo(getYoloMode());
    window.addEventListener(YOLO_EVENT, sync);
    return () => window.removeEventListener(YOLO_EVENT, sync);
  }, []);

  useEffect(() => {
    let disposed = false;
    setLoading(true); setLoadError(false);
    api.getPreferences().then((prefs) => {
      if (disposed) return;
      const tz = prefs.tz_offset_hours ?? 8;
      setOffset(tz);
      setTzOffset(tz);
    }).catch(() => { if (!disposed) setLoadError(true); }).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [loadAttempt]);

  useEffect(() => {
    if (section !== 'preferences') return;
    timerRef.current = setInterval(() => setTick(t => t + 1), 1000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [section]);

  const handleTzChange = async (val: number) => {
    setSaving(true);
    try {
      await api.updatePreferences({ tz_offset_hours: val });
      setOffset(val); setTzOffset(val);
      message.success(t('common.saved', '已保存'));
    } catch { message.error(t('settingsPolish.saveFailed')); }
    setSaving(false);
  };

  if (loading) {
    return <LogoLoading size={240} />;
  }

  void tick;
  const nowIso = new Date().toISOString();
  const serverUtcStr = nowIso.replace('T', ' ').slice(0, 19);
  const userTimeStr = fmtUserTime(nowIso, 'datetime');

  const cardBase: React.CSSProperties = {
    background: C.bg1,
    borderRadius: 'var(--jf-radius-lg)',
    border: `1px solid ${C.border}`,
    padding: isMobile ? '16px 14px' : '20px 24px',
    marginBottom: 16,
  };
  const gridCols = isMobile ? '80px 1fr' : '100px 1fr';

  return (
    <div className="settings-page general-settings">
      {loadError && <SettingsLoadError onRetry={() => setLoadAttempt(n => n + 1)} />}
      <SettingsSectionNav label={t('general.pageTitle')} value={section} onChange={setSection}
        items={['engine', 'preferences', 'automation', 'advanced'].map(value => ({ value, label: t(`settingsPolish.${value}`), description: t(`settingsDesign.${value}Hint`) }))} />
      <section hidden={section !== 'preferences'} aria-label={t('settingsPolish.preferences')}>

      {/* Interface language */}
      <div style={cardBase}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Translate size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>{t('general.languageCardTitle')}</Text>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <LanguageSwitcher variant="compact" />
          <Text style={{ color: C.muted, fontSize: 12, flex: 1, minWidth: 200 }}>
            {t('general.languageDesc')}
          </Text>
        </div>
      </div>

      </section>
      <section hidden={section !== 'engine'} aria-label={t('settingsPolish.engine')}>
      {/* API Keys */}
      <RuntimeSettingsCard />
      <ApiKeysCard />

      <details className="settings-disclosure"><summary>{t('settingsPolish.modelCatalog')}</summary>
      {/* Aggregator whitelists (search latest + enable) */}
      <AggregatorModelsCard provider="openrouter" />
      <AggregatorModelsCard provider="siliconflow" showThinkingBudget />

      {/* Model Visibility */}
      <ModelVisibilityCard />
      </details>

      </section>
      <section hidden={section !== 'preferences'} aria-label={t('settingsPolish.preferences')}>
      {/* Time & Timezone */}
      <div style={cardBase}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <Clock size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>{t('general.timezoneCardTitle')}</Text>
          {saving && <Spin size="small" style={{ marginLeft: 8 }} />}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: gridCols, gap: '12px 16px', alignItems: 'center' }}>
          <Text style={{ color: C.muted, fontSize: 13 }}>{t('general.tzOffset')}</Text>
          <Select
            aria-label={t('general.tzOffset')}
            disabled={saving || loadError}
            value={offset}
            onChange={handleTzChange}
            style={{ maxWidth: 360, width: '100%' }}
            options={TZ_OPTIONS}
            showSearch
            optionFilterProp="label"
          />

          <Text style={{ color: C.muted, fontSize: 13 }}>{t('general.serverUtc')}</Text>
          <div>
            <Tag color="blue" style={{ fontFamily: "'Cascadia Code', monospace", fontSize: 13 }}>
              {serverUtcStr}
            </Tag>
          </div>

          <Text style={{ color: C.muted, fontSize: 13 }}>{t('general.userTime')}</Text>
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
            {t('general.tzNote')}
          </Text>
        </div>
      </div>

      {/* UI Style */}
      <div style={{ ...cardBase, marginBottom: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <PaintBrush size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>{t('general.uiStyleCardTitle')}</Text>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: gridCols, gap: '12px 16px', alignItems: 'center' }}>
          <Text style={{ color: C.muted, fontSize: 13 }}>{t('general.uiStyleLabel')}</Text>
          <Select
            value={uiStyle}
            onChange={(v: UiStyle) => setUiStyle(v)}
            style={{ maxWidth: 360, width: '100%' }}
            options={styleOptions}
          />
        </div>

        <div style={{ marginTop: 14 }}>
          <Text style={{ color: C.muted, fontSize: 12 }}>
            {t('general.uiStyleNote')}
          </Text>
        </div>
      </div>

      </section>
      <section hidden={section !== 'advanced'} aria-label={t('settingsPolish.advanced')}>
      {/* YOLO mode (admin) */}
      <div style={{ ...cardBase, marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <Lightning size={18} color={C.primary} weight={yolo ? 'fill' : 'regular'} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>{t('general.yoloCardTitle')}</Text>
          {yolo && (
            <Tag color="orange" style={{ fontSize: 11, lineHeight: '16px', padding: '0 6px', marginLeft: 4 }}>
              {t('general.yoloEnabled')}
            </Tag>
          )}
        </div>

        <div style={{
          display: 'flex',
          alignItems: isMobile ? 'flex-start' : 'center',
          justifyContent: 'space-between',
          gap: isMobile ? 12 : 16,
          flexDirection: isMobile ? 'column-reverse' : 'row',
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ color: C.muted, fontSize: 12, display: 'block' }}>
              {t('general.yoloDesc')}
            </Text>
            <Text style={{ color: 'var(--jf-warning, #d4a017)', fontSize: 12, display: 'block', marginTop: 6 }}>
              {t('general.yoloWarning')}
            </Text>
            <Text style={{ color: C.muted, fontSize: 11, display: 'block', marginTop: 6 }}>
              {t('general.yoloScope')}
            </Text>
          </div>
          <Switch
            checked={yolo}
            onChange={(v) => { setYolo(v); setYoloMode(v); }}
            style={{ flexShrink: 0 }}
          />
        </div>
      </div>

      </section>
      <section hidden={section !== 'automation'} aria-label={t('settingsPolish.automation')}>
      {/* Batch run */}
      <div style={{ ...cardBase, marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <Lightning size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>{t('general.batchCardTitle')}</Text>
        </div>
        <Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 16 }}>
          {t('general.batchDesc')}
        </Text>
        <BatchRunner open onClose={() => {}} inline />
      </div>

      </section>
      <section hidden={section !== 'advanced'} aria-label={t('settingsPolish.advanced')}>
      {/* Advanced Pages */}
      <div style={{ ...cardBase, marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <Sliders size={18} color={C.primary} />
          <Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>{t('general.advancedCardTitle')}</Text>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <Text style={{ color: C.text, fontSize: 13 }}>{t('general.advOpRules')}</Text>
              <Tag color="purple" style={{ marginLeft: 6, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>Advanced</Tag>
              <div>
                <Text style={{ color: C.muted, fontSize: 12 }}>
                  {t('general.advOpRulesDesc')}
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
              <Text style={{ color: C.text, fontSize: 13 }}>{t('general.advMemorySoul')}</Text>
              <Tag color="purple" style={{ marginLeft: 6, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>Advanced</Tag>
              <div>
                <Text style={{ color: C.muted, fontSize: 12 }}>
                  {t('general.advMemorySoulDesc')}
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
      </section>
    </div>
  );
}

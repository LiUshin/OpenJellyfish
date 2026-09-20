import SettingsSectionNav from '../../components/SettingsSectionNav';
import SettingsLoadError from '../../components/SettingsLoadError';
import { useTranslation } from 'react-i18next';
import { useEffect, useState, useCallback } from 'react';
import {
  Card, ConfigProvider, Switch, Input, Button, Select, InputNumber, Space, Alert, message, Spin, Popconfirm, Typography, Tag, AutoComplete,
} from 'antd';
import { Phone, ArrowCounterClockwise, FloppyDisk } from '@phosphor-icons/react';
import * as api from '../../services/api';
import type { VoiceAgentConfig } from '../../services/api';
import type { ModelInfo } from '../../types';
import VoiceCallModal from '../../components/VoiceCallModal';

const { TextArea } = Input;
const { Text } = Typography;

const OPENAI_VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'];

// CosyVoice v2 常用音色(可在下拉编辑/手填任意音色参数)
const COSYVOICE_VOICES = [
  { value: 'longxiaochun_v2', label: 'longxiaochun_v2（龙小淳·女）' },
  { value: 'longxiaoxia_v2', label: 'longxiaoxia_v2（龙小夏·女）' },
  { value: 'longwan_v2', label: 'longwan_v2（龙婉·女）' },
  { value: 'longcheng_v2', label: 'longcheng_v2（龙橙·男）' },
  { value: 'longhua_v2', label: 'longhua_v2（龙华·女）' },
  { value: 'longshu_v2', label: 'longshu_v2（龙书·男）' },
  { value: 'longjing_v2', label: 'longjing_v2（龙婧·女）' },
  { value: 'longyue_v2', label: 'longyue_v2（龙悦·女）' },
  { value: 'longyuan_v2', label: 'longyuan_v2（龙媛·女）' },
  { value: 'loongbella_v2', label: 'loongbella_v2（Bella·女）' },
];

function linesToList(text: string): string[] {
  return text.split('\n').map((s) => s.trim()).filter(Boolean);
}

/** 语音前台调音台:编辑前台 Copilot 配置 + 连接状态 + 试通话。 */
export default function VoicePage() {
  const { t } = useTranslation();
  const [section, setSection] = useState('conversation');
  const [loadError, setLoadError] = useState(false);
  const [savedConfig, setSavedConfig] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [cfg, setCfg] = useState<VoiceAgentConfig | null>(null);
  const [callOpen, setCallOpen] = useState(false);
  // 可选 LLM 模型(来自 OpenJellyfish /api/models,仅保留 worker 支持的供应商)
  const [llmModels, setLlmModels] = useState<ModelInfo[]>([]);

  // fillers 用多行文本编辑(每行一句)
  const [delegating, setDelegating] = useState('');
  const [toolRunning, setToolRunning] = useState('');
  const [longTask, setLongTask] = useState('');

  // Fish 音色库:新增音色的输入(标签 + ID)
  const [newFishLabel, setNewFishLabel] = useState('');
  const [newFishId, setNewFishId] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const [status, c, modelsRes] = await Promise.all([
        api.getVoiceLiveStatus(),
        api.getVoiceAgentConfig(),
        api.getModels().catch(() => ({ models: [], default: '' })),
      ]);
      setConfigured(status.configured);
      setCfg(c);
      setSavedConfig(JSON.stringify(c));
      // 与 Chat 模型表完全一致(worker 已支持 openai/kimi/bedrock/anthropic/minimax 全部供应商)
      setLlmModels(modelsRes.models || []);
      setDelegating((c.fillers?.delegating || []).join('\n'));
      setToolRunning((c.fillers?.tool_running || []).join('\n'));
      setLongTask((c.fillers?.long_task || []).join('\n'));
    } catch (e) {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const patch = useCallback(<K extends keyof VoiceAgentConfig>(key: K, value: VoiceAgentConfig[K]) => {
    setCfg((prev) => (prev ? { ...prev, [key]: value } : prev));
  }, []);

  const save = useCallback(async () => {
    if (!cfg || saving) return;
    setSaving(true);
    try {
      const payload: Partial<VoiceAgentConfig> = {
        ...cfg,
        fillers: {
          delegating: linesToList(delegating),
          tool_running: linesToList(toolRunning),
          long_task: linesToList(longTask),
        },
      };
      const updated = await api.updateVoiceAgentConfig(payload);
      setCfg(updated);
      setSavedConfig(JSON.stringify(updated));
      setDelegating((updated.fillers?.delegating || []).join('\n'));
      setToolRunning((updated.fillers?.tool_running || []).join('\n'));
      setLongTask((updated.fillers?.long_task || []).join('\n'));
      message.success('已保存,下一通通话即时生效');
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }, [cfg, delegating, toolRunning, longTask, saving]);

  const reset = useCallback(async () => {
    setSaving(true);
    try {
      const c = await api.resetVoiceAgentConfig();
      setCfg(c);
      setSavedConfig(JSON.stringify(c));
      setDelegating((c.fillers?.delegating || []).join('\n'));
      setToolRunning((c.fillers?.tool_running || []).join('\n'));
      setLongTask((c.fillers?.long_task || []).join('\n'));
      message.success('已恢复默认');
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }, []);

  if (loadError) return <div className="settings-state"><SettingsLoadError onRetry={() => void load()} /></div>;
  if (loading || !cfg) {
    return <div style={{ padding: 40, textAlign: 'center' }}><Spin /></div>;
  }

  const draft = { ...cfg, fillers: { delegating: linesToList(delegating), tool_running: linesToList(toolRunning), long_task: linesToList(longTask) } };
  const dirty = JSON.stringify(draft) !== savedConfig;
  return (
    <div className="settings-page voice-settings">
      {!configured && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={t('settingsDesign.voiceSetup')}
          description={<><span>{t('settingsDesign.voiceSetupHint')}</span><details className="settings-inline-help"><summary>连接配置</summary><p>请在服务端配置 LiveKit 与语音 Worker 的连接参数。</p></details></>}
        />
      )}

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space align="center" style={{ justifyContent: 'space-between', width: '100%' }}>
          <Space>
            <Text strong>启用语音前台</Text>
            <Switch aria-label="启用语音前台" disabled={saving} checked={cfg.enabled} onChange={(v) => patch('enabled', v)} />
          </Space>
          <Button
            type="primary"
            icon={<Phone size={16} />}
            disabled={!configured || !cfg.enabled || dirty || saving}
            onClick={() => setCallOpen(true)}
          >
            试通话
          </Button>
        </Space>
      </Card>

      <SettingsSectionNav label={t('settingsPolish.pages.voice.title')} value={section} onChange={setSection} items={[
        { value: 'conversation', label: t('settingsDesign.voiceConversation'), description: t('settingsDesign.voiceConversationHint') },
        { value: 'behavior', label: t('settingsDesign.voiceBehavior'), description: t('settingsDesign.voiceBehaviorHint') },
        { value: 'models', label: t('settingsDesign.voiceModels'), description: t('settingsDesign.voiceModelsHint') },
      ]} />
      <ConfigProvider componentDisabled={saving}><fieldset disabled={saving} className="settings-form-fields">
      <section hidden={section !== 'conversation'}>
      <Card size="small" title="开场白" style={{ marginBottom: 16 }}>
        <Input aria-label="开场白" value={cfg.greeting} onChange={(e) => patch('greeting', e.target.value)} placeholder="接通后的第一句话" />
      </Card>

      <Card size="small" title="前台人格 (System Prompt)" style={{ marginBottom: 16 }}>
        <TextArea
          aria-label="前台人格" value={cfg.system_prompt}
          onChange={(e) => patch('system_prompt', e.target.value)}
          autoSize={{ minRows: 3, maxRows: 8 }}
        />
      </Card>

      <Card size="small" title="路由策略 (闲聊直答 vs 委派)" style={{ marginBottom: 16 }}>
        <TextArea
          aria-label="路由策略" value={cfg.routing_policy}
          onChange={(e) => patch('routing_policy', e.target.value)}
          autoSize={{ minRows: 3, maxRows: 8 }}
        />
      </Card>

      </section>
      <section hidden={section !== 'behavior'}>
      <Card size="small" title="填充语 (每行一句,等待时随机选用)" style={{ marginBottom: 16 }}>
        <Text type="secondary">委派承接语</Text>
        <TextArea aria-label="委派承接语" value={delegating} onChange={(e) => setDelegating(e.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} style={{ marginBottom: 12 }} />
        <Text type="secondary">工具运行中</Text>
        <TextArea aria-label="工具运行中" value={toolRunning} onChange={(e) => setToolRunning(e.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} style={{ marginBottom: 12 }} />
        <Text type="secondary">长任务安抚</Text>
        <TextArea aria-label="长任务安抚" value={longTask} onChange={(e) => setLongTask(e.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} />
      </Card>

      <Card size="small" title="打断行为" style={{ marginBottom: 16 }}>
        <Space size="large" wrap>
          <Space>
            <Text>允许打断</Text>
            <Switch
              aria-label="允许打断" checked={cfg.interruption.allow_interruptions}
              onChange={(v) => patch('interruption', { ...cfg.interruption, allow_interruptions: v })}
            />
          </Space>
          <Space>
            <Text>实质打断所需词数</Text>
            <InputNumber
              min={1}
              max={10}
              aria-label="实质打断所需词数" value={cfg.interruption.min_interruption_words}
              onChange={(v) => patch('interruption', { ...cfg.interruption, min_interruption_words: Number(v) || 1 })}
            />
          </Space>
        </Space>
      </Card>

      </section>
      <section hidden={section !== 'models'}>
      <Card size="small" title="模型与音色" style={{ marginBottom: 16 }}>
        <Space size="large" wrap>
          <Space>
            <Text>前台 LLM 模型</Text>
            <Select
              aria-label="前台 LLM 模型"
              style={{ width: 280 }}
              showSearch
              placeholder="选择模型"
              value={cfg.providers.llm_model || undefined}
              onChange={(v) => {
                const provider = v.includes(':') ? v.split(':')[0] : (cfg.providers.llm || 'openai');
                patch('providers', { ...cfg.providers, llm_model: v, llm: provider });
              }}
              filterOption={(input, option) =>
                String(option?.label ?? '').toLowerCase().includes(input.toLowerCase()) ||
                String(option?.value ?? '').toLowerCase().includes(input.toLowerCase())
              }
              options={(() => {
                const opts = llmModels.map((m) => ({
                  value: m.id,
                  label: `${m.name}（${m.provider}）`,
                }));
                const cur = cfg.providers.llm_model;
                if (cur && !opts.some((o) => o.value === cur)) {
                  opts.unshift({ value: cur, label: `（当前）${cur}` });
                }
                return opts;
              })()}
              notFoundContent={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  无可用模型,请先在「设置 → 通用设置」配置对应供应商凭据
                </Text>
              }
            />
          </Space>
          <Space>
            <Text>STT 供应商</Text>
            <Select
              style={{ width: 140 }}
              aria-label="STT 供应商" value={cfg.providers.stt || 'openai'}
              onChange={(v) => {
                // stt_model 跨供应商共享:切换时重置为该供应商默认,避免残留模型名
                // (如阿里 paraformer-realtime-v2)被带给 OpenAI STT 触发 404。
                const sttDefaults: Record<string, string> = {
                  openai: 'gpt-4o-mini-transcribe',
                  fishaudio: '',
                  aliyun: 'paraformer-realtime-v2',
                };
                patch('providers', {
                  ...cfg.providers,
                  stt: v,
                  stt_model: sttDefaults[v] ?? '',
                });
              }}
              options={[
                { value: 'openai', label: 'OpenAI' },
                { value: 'fishaudio', label: 'Fish Audio' },
                { value: 'aliyun', label: '阿里云 Paraformer' },
              ]}
            />
          </Space>
          {cfg.providers.stt !== 'fishaudio' && (
            <Space>
              <Text>STT 模型</Text>
              <Input
                style={{ width: 200 }}
                placeholder={cfg.providers.stt === 'aliyun' ? 'paraformer-realtime-v2' : 'gpt-4o-mini-transcribe'}
                aria-label="STT 模型" value={cfg.providers.stt_model}
                onChange={(e) => patch('providers', { ...cfg.providers, stt_model: e.target.value })}
              />
            </Space>
          )}
          <Space>
            <Text>TTS 供应商</Text>
            <Select
              style={{ width: 140 }}
              aria-label="TTS 供应商" value={cfg.providers.tts || 'openai'}
              onChange={(v) => {
                // tts_model 跨供应商共享:切换时重置为该供应商默认模型,避免把
                // 上一个供应商的模型名(如 Fish 的 s2-pro)带给 OpenAI 触发 404。
                const ttsDefaults: Record<string, string> = {
                  openai: 'gpt-4o-mini-tts',
                  fishaudio: 's1',
                  aliyun: 'cosyvoice-v2',
                };
                patch('providers', {
                  ...cfg.providers,
                  tts: v,
                  tts_model: ttsDefaults[v] || '',
                });
              }}
              options={[
                { value: 'openai', label: 'OpenAI' },
                { value: 'fishaudio', label: 'Fish Audio' },
                { value: 'aliyun', label: '阿里云 CosyVoice' },
              ]}
            />
          </Space>
          <Space>
            <Text>TTS 模型</Text>
            <Input
              style={{ width: 180 }}
              placeholder={
                cfg.providers.tts === 'fishaudio'
                  ? 's1 / s2-pro'
                  : cfg.providers.tts === 'aliyun'
                  ? 'cosyvoice-v2'
                  : 'gpt-4o-mini-tts'
              }
              aria-label="TTS 模型" value={cfg.providers.tts_model || ''}
              onChange={(e) => patch('providers', { ...cfg.providers, tts_model: e.target.value })}
            />
          </Space>
          {cfg.providers.tts === 'fishaudio' && (
            <Space>
              <Text>当前音色</Text>
              <Select
                style={{ width: 220 }}
                allowClear
                placeholder="选择已存音色（留空=默认）"
                value={cfg.providers.fish_reference_id || undefined}
                onChange={(v) => patch('providers', { ...cfg.providers, fish_reference_id: v || '' })}
                options={(() => {
                  const list = (cfg.providers.fish_voices || []).map((fv) => ({
                    value: fv.id,
                    label: `${fv.label || '未命名'}（${fv.id}）`,
                  }));
                  const cur = cfg.providers.fish_reference_id;
                  if (cur && !list.some((o) => o.value === cur)) {
                    list.unshift({ value: cur, label: `（当前）${cur}` });
                  }
                  return list;
                })()}
              />
            </Space>
          )}
          {cfg.providers.tts === 'aliyun' && (
            <Space>
              <Text>CosyVoice 音色</Text>
              <AutoComplete
                style={{ width: 240 }}
                placeholder="longcheng_v2"
                options={COSYVOICE_VOICES}
                filterOption={(input, option) =>
                  (option?.value as string).toLowerCase().includes(input.toLowerCase())
                }
                value={cfg.providers.aliyun_tts_voice || ''}
                onChange={(v) => patch('providers', { ...cfg.providers, aliyun_tts_voice: v })}
              />
            </Space>
          )}
          {cfg.providers.tts === 'openai' && (
            <Space>
              <Text>TTS 音色</Text>
              <Select
                style={{ width: 140 }}
                aria-label="TTS 音色" value={cfg.providers.tts_voice}
                onChange={(v) => patch('providers', { ...cfg.providers, tts_voice: v })}
                options={OPENAI_VOICES.map((v) => ({ value: v, label: v }))}
              />
            </Space>
          )}
        </Space>
        {cfg.providers.tts === 'fishaudio' && (
          <div style={{ marginTop: 12 }}>
            <Text strong style={{ display: 'block', marginBottom: 6 }}>Fish 音色库</Text>
            <Space wrap style={{ marginBottom: 8 }}>
              {(cfg.providers.fish_voices || []).length === 0 && (
                <Text type="secondary" style={{ fontSize: 12 }}>暂无保存的音色，在下方添加。</Text>
              )}
              {(cfg.providers.fish_voices || []).map((fv) => {
                const active = cfg.providers.fish_reference_id === fv.id;
                return (
                  <Tag
                    key={fv.id}
                    color={active ? 'blue' : 'default'}
                    closable
                    onClose={(e) => {
                      e.preventDefault();
                      const next = (cfg.providers.fish_voices || []).filter((x) => x.id !== fv.id);
                      const stillActive = active ? '' : cfg.providers.fish_reference_id || '';
                      patch('providers', { ...cfg.providers, fish_voices: next, fish_reference_id: stillActive });
                    }}
                    onClick={() => patch('providers', { ...cfg.providers, fish_reference_id: fv.id })}
                    style={{ cursor: 'pointer', userSelect: 'none' }}
                  >
                    {active ? '✓ ' : ''}{fv.label || '未命名'}（{fv.id}）
                  </Tag>
                );
              })}
            </Space>
            <Space wrap>
              <Input
                style={{ width: 160 }}
                placeholder="标签（如 客服女声）"
                aria-label="音色标签" value={newFishLabel}
                onChange={(e) => setNewFishLabel(e.target.value)}
              />
              <Input
                style={{ width: 240 }}
                placeholder="reference_id"
                aria-label="音色 ID" value={newFishId}
                onChange={(e) => setNewFishId(e.target.value)}
              />
              <Button
                onClick={() => {
                  const id = newFishId.trim();
                  if (!id) { message.warning('请填写音色 reference_id'); return; }
                  const list = cfg.providers.fish_voices || [];
                  if (list.some((x) => x.id === id)) { message.warning('该音色 ID 已存在'); return; }
                  const next = [...list, { id, label: newFishLabel.trim() || id }];
                  // 新增即设为当前音色（若当前为空）
                  const active = cfg.providers.fish_reference_id || id;
                  patch('providers', { ...cfg.providers, fish_voices: next, fish_reference_id: active });
                  setNewFishLabel('');
                  setNewFishId('');
                }}
              >
                添加音色
              </Button>
            </Space>
            <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
              点击标签设为当前音色（蓝色✓为当前），× 删除。保存后下一通通话生效。
            </Text>
          </div>
        )}
        {(cfg.providers.tts === 'fishaudio' || cfg.providers.stt === 'fishaudio') && (
          <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
            需在服务端 .env 配置 FISH_API_KEY（STT 与 TTS 共用同一把 key；音色 ID 可在 fish.audio 控制台获取，留空用通用音色）。
          </Text>
        )}
        {cfg.providers.stt === 'fishaudio' && (
          <Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>
            注意：Fish Audio STT 为批量识别（非流式），需等停顿后整段上传，延迟高于流式 STT。
          </Text>
        )}
        {(cfg.providers.stt === 'aliyun' || cfg.providers.tts === 'aliyun') && (
          <Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>
            阿里云 Paraformer(STT)/CosyVoice(TTS) 为流式、端点在国内，需在服务端 .env 配置 DASHSCOPE_API_KEY（STT/TTS 共用同一把 key）。
          </Text>
        )}
        <Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>
          前台 LLM 模型下拉与 Chat 完全一致（OpenAI / Anthropic / Kimi / MiniMax / Bedrock）；没出现的模型请先在「设置 → 通用设置」配置对应凭据。思考(-thinking)型号在实时语音里按基础模型运行以降低延迟。
        </Text>
      </Card>

      </section>
      </fieldset></ConfigProvider>
      <div className="settings-savebar">
        <span role="status">{t(dirty ? 'settingsPolish.unsaved' : 'settingsPolish.saved')} · {t('settingsPolish.voiceSaveHint')}</span>
        <Button type="primary" icon={<FloppyDisk size={16} />} loading={saving} disabled={!dirty} onClick={save}>{t('common.save')}</Button>
        <Popconfirm title={t('settingsPolish.resetConfirm')} onConfirm={reset} okText={t('common.confirm')} cancelText={t('common.cancel')}>
          <Button icon={<ArrowCounterClockwise size={16} />} disabled={saving}>恢复默认</Button>
        </Popconfirm>
      </div>

      <VoiceCallModal
        open={callOpen}
        conversationId={`voice-test-${Date.now()}`}
        onClose={() => setCallOpen(false)}
      />
    </div>
  );
}

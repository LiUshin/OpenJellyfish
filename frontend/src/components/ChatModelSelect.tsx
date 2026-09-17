import { Select } from 'antd';
import type { RuntimeChoice, RuntimeProfile } from '../services/runtime';

const key = (choice: RuntimeChoice) => JSON.stringify([choice.runtime, choice.profile_id || '', choice.model || '']);

/** One model picker in the chat composer; connection management stays in settings. */
export default function ChatModelSelect({ value, onChange, models = [], profiles, disabled, loading, bound = false }: {
  value: RuntimeChoice;
  onChange: (choice: RuntimeChoice) => void;
  models?: { id: string; name: string }[];
  profiles: RuntimeProfile[];
  disabled?: boolean;
  loading?: boolean;
  bound?: boolean;
}) {
  const options: { label: string; options: { value: string; label: string; disabled?: boolean }[] }[] = [
    ...(!bound || value.runtime === 'deepagents' ? [{ label: 'API 模型', options: models.map(m => ({
      value: key({ runtime: 'deepagents', model: m.id }), label: m.name,
    })) }] : []),
    ...profiles.filter(p => p.status === 'ready' && !p.recovery_required && (!bound || p.id === value.profile_id))
      .map(p => ({ label: `${p.runtime === 'cursor' ? 'Cursor' : 'Codex'} · ${p.name}`, options: p.models.map(m => ({
        value: key({ runtime: p.runtime, profile_id: p.id, model: m.id }),
        label: `${p.runtime === 'cursor' ? 'Cursor' : 'Codex'} · ${m.name}`,
      })) })),
  ];
  const selected = value.model ? key(value) : undefined;
  if (selected && !options.some(g => g.options.some(o => o.value === selected))) {
    options.push({ label: '当前选择', options: [{ value: selected, label: `${value.model}（暂不可用）`, disabled: true }] });
  }
  return <Select aria-label="聊天模型" showSearch optionFilterProp="label" value={selected}
    loading={loading} disabled={disabled} placeholder="选择模型" size="small"
    style={{ minWidth: 200, maxWidth: '100%' }} popupMatchSelectWidth={false} options={options}
    onChange={encoded => { const [runtime, profile_id, model] = JSON.parse(encoded); onChange({ runtime, profile_id: profile_id || undefined, model, ...(runtime !== 'deepagents' ? { image_mode: 'native' as const } : {}) }); }} />;
}

import { Alert, Select, Space, Typography } from 'antd';
import type { RuntimeChoice, RuntimeProfile } from '../services/runtime';

export default function RuntimeChoiceFields({ value = { runtime: 'deepagents' }, onChange = () => {}, profiles, deepModels = [], serviceMode = false }: {
  value?: RuntimeChoice; onChange?: (choice: RuntimeChoice) => void; profiles: RuntimeProfile[];
  deepModels?: { id: string; name: string }[]; serviceMode?: boolean;
}) {
  const profile = profiles.find(p => p.id === value.profile_id && p.runtime === value.runtime);
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Select aria-label="聊天引擎" style={{ width: '100%' }} value={value.runtime} onChange={v => onChange({ runtime: v, image_mode: 'native' })}
      options={[{ value: 'deepagents', label: 'DeepAgents' }, { value: 'codex', label: 'Codex' }, { value: 'cursor', label: 'Cursor' }]} />
    {value.runtime !== 'deepagents' ? <>
      <Select aria-label="引擎连接" placeholder="选择可用连接" style={{ width: '100%' }} value={value.profile_id}
        onChange={v => onChange({ ...value, profile_id: v, model: undefined })}
        options={profiles.filter(p => p.runtime === value.runtime).map(p => ({ value: p.id, label: `${p.name} · ${p.source === 'personal' ? '本人' : '团队'}`, disabled: p.status !== 'ready' || p.recovery_required }))} />
      <Select aria-label="引擎模型" placeholder="选择模型" style={{ width: '100%' }} value={value.model} onChange={v => onChange({ ...value, model: v })}
        options={(profile?.models || []).map(m => ({ value: m.id, label: m.name }))} />
      {(!profile || profile.status !== 'ready' || profile.recovery_required) && <Alert type="info" message="请在设置中连接此引擎，或由超管授权。" />}
      <Typography.Text type="secondary">{serviceMode ? "仅展示超管已授权的连接与模型。请在下方勾选联网搜索、生图等服务能力。" : "支持原生网页搜索、图片和文件输入；生图能力取决于客户端和账号。同一连接内可逐条切换模型。"}</Typography.Text>
    </> : deepModels.length > 0 && <Select aria-label="DeepAgents 模型" placeholder="使用默认模型" style={{ width: '100%' }} value={value.model}
      onChange={v => onChange({ ...value, model: v })} options={deepModels.map(m => ({ value: m.id, label: m.name }))} />}
  </Space>;
}

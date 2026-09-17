import { useEffect, useState } from 'react';
import { Alert, Button, Modal, Space, Tag, Typography } from 'antd';
import * as runtime from '../../../services/runtime';
import * as api from '../../../services/api';
import type { Conversation } from '../../../types';
import RuntimeChoiceFields from '../../../components/RuntimeChoiceFields';
import FileTreePicker from '../../../components/FileTreePicker';

export default function NewRuntimeChat({ onClose, onCreated, models, defaultModel }: {
  onClose: () => void; onCreated: (c: Conversation) => void; models: { id: string; name: string }[]; defaultModel: string;
}) {
  const [choice, setChoice] = useState<runtime.RuntimeChoice>({ runtime: 'deepagents', model: defaultModel || undefined });
  const [profiles, setProfiles] = useState<runtime.RuntimeProfile[]>([]);
  const [paths, setPaths] = useState<string[]>([]);
  const [picker, setPicker] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    runtime.capabilities().then(async c => {
      if (!c.available) return;
      const [p, pref] = await Promise.all([runtime.profiles(), runtime.preferences()]);
      if (!disposed) { setProfiles(p); setChoice(pref.runtime === 'deepagents' ? { ...pref, model: pref.model || defaultModel || undefined } : pref); }
    }).catch(e => { if (!disposed) setError(e.message); }).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [defaultModel]);
  async function create() {
    setBusy(true); setError('');
    try { onCreated(await api.createConversation('新对话', choice, choice.runtime !== 'deepagents' ? paths : [])); }
    catch (e) { setError(e instanceof Error ? e.message : '创建失败'); }
    finally { setBusy(false); }
  }
  return <>
    <Modal open title="新建聊天" onCancel={onClose} onOk={() => void create()} okText="创建聊天" confirmLoading={busy} okButtonProps={{ disabled: loading || (choice.runtime !== 'deepagents' && (!choice.profile_id || !choice.model)) }}>
      <Space direction="vertical" style={{ width: '100%' }}>
        {error && <Alert type="error" message={error} />}
        <RuntimeChoiceFields value={choice} onChange={setChoice} profiles={profiles} deepModels={models} />
        {choice.runtime !== 'deepagents' && <>
          <Button onClick={() => setPicker(true)}>选择文档副本</Button>
          <Typography.Text type="secondary">选择具体文件，最多 20 个。修改副本后会生成独立产物。</Typography.Text>
          <Space wrap>{paths.map(p => <Tag key={p} closable onClose={() => setPaths(v => v.filter(x => x !== p))}>{p}</Tag>)}</Space>
        </>}
      </Space>
    </Modal>
    <FileTreePicker open={picker} title="导入文档副本（选择具体文件）" rootPath="/docs" pathOutput="absolute" value={paths} onCancel={() => setPicker(false)}
      onOk={p => { if (p.length > 20) { setError('最多选择 20 个文档'); return; } setPaths(p); setPicker(false); }} />
  </>;
}

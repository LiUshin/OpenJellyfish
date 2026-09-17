import { useEffect, useState } from 'react';
import { Alert, Button, Typography } from 'antd';
import RuntimeChoiceFields from './RuntimeChoiceFields';
import * as runtime from '../services/runtime';

export default function RuntimeDefault({ profiles }: { profiles: runtime.RuntimeProfile[] }) {
  const [choice, setChoice] = useState<runtime.RuntimeChoice>({ runtime: 'deepagents' });
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let disposed = false;
    runtime.preferences().then(p => { if (!disposed) { setChoice(p); setReady(true); } })
      .catch(e => { if (!disposed) setNotice(e.message); });
    return () => { disposed = true; };
  }, []);
  return <div style={{ borderTop: '1px solid var(--jf-border)', marginTop: 20, paddingTop: 16 }}>
    <Typography.Paragraph strong>新聊天默认引擎</Typography.Paragraph>
    <RuntimeChoiceFields value={choice} onChange={c => { setChoice(c); setNotice(''); }} profiles={profiles} />
    <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>仅影响之后创建的会话，已有会话保留原连接和模型。</Typography.Paragraph>
    {notice && <Alert type="info" message={notice} style={{ marginBottom: 8 }} />}
    <Button disabled={!ready} loading={busy} onClick={async () => {
      setBusy(true); setNotice('');
      try { await runtime.savePreferences(choice); setNotice('已保存新聊天默认引擎'); }
      catch (e) { setNotice(e instanceof Error ? e.message : '保存失败'); }
      finally { setBusy(false); }
    }}>保存默认引擎</Button>
  </div>;
}

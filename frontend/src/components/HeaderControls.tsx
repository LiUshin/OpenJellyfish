import { Button, Tooltip } from 'antd';
import { FolderOpen } from '@phosphor-icons/react';
import SplitToggle from './SplitToggle';
import { useFileWorkspace } from '../stores/fileWorkspaceContext';

export default function HeaderControls() {
  const {
    editingFile,
    splitMode,
    setSplitMode,
    fileBrowserOpen,
    setFileBrowserOpen,
  } = useFileWorkspace();

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
      {!!editingFile && (
        <>
          <SplitToggle value={splitMode} onChange={setSplitMode} />
          <div style={{
            width: 1,
            height: 16,
            background: 'var(--jf-border)',
            flexShrink: 0,
          }} />
        </>
      )}
      <Tooltip title={fileBrowserOpen ? '关闭文件面板' : '文件面板'}>
        <Button
          type="text"
          size="small"
          icon={<FolderOpen size={16} />}
          onClick={() => setFileBrowserOpen((v: boolean) => !v)}
          style={{
            color: fileBrowserOpen ? 'var(--jf-primary)' : 'var(--jf-text-muted)',
            width: 28,
            height: 28,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        />
      </Tooltip>
    </div>
  );
}

import { Button, Tooltip } from 'antd';
import { FolderOpen } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import SplitToggle from './SplitToggle';
import { useFileWorkspace } from '../stores/fileWorkspaceContext';
import { useIsMobile } from '../hooks/useMediaQuery';

export default function HeaderControls() {
  const {
    editingFile,
    splitMode,
    setSplitMode,
    fileBrowserOpen,
    setFileBrowserOpen,
  } = useFileWorkspace();
  const isMobile = useIsMobile();
  const { t } = useTranslation();

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
      {/* Split toggle只在桌面端有意义：移动端文件预览本身就是全屏 Drawer */}
      {!!editingFile && !isMobile && (
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
      <Tooltip title={fileBrowserOpen ? t('header.closeFilePanel') : t('header.openFilePanel')}>
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

import { type KeyboardEvent } from 'react';
import { Button, Tooltip } from 'antd';
import { SaveOutlined, CloseOutlined } from '@ant-design/icons';
import { useFileWorkspace } from '../stores/fileWorkspaceContext';
import HeaderControls from './HeaderControls';

const C = {
  bg: 'var(--jf-bg-panel)',
  bgDark: 'var(--jf-bg-deep)',
  text: 'var(--jf-text)',
  textSec: 'var(--jf-text-muted)',
  textDim: 'var(--jf-text-dim)',
  border: 'var(--jf-border)',
  accent: 'var(--jf-legacy)',
};

export default function FilePreview() {
  const {
    editingFile,
    editContent,
    editDirty,
    saving,
    saveFile,
    closeFile,
    setEditContent,
  } = useFileWorkspace();

  if (!editingFile) return null;

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      saveFile();
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: C.bg,
        minWidth: 0,
      }}
    >
      <div
        style={{
          padding: '0 14px',
          height: 47,
          boxSizing: 'border-box',
          borderBottom: `1px solid ${C.border}`,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexShrink: 0,
        }}
      >
        <Tooltip title={editDirty ? '保存 (Ctrl+S)' : '已保存'}>
          <Button
            type="text"
            size="small"
            icon={<SaveOutlined />}
            disabled={!editDirty}
            loading={saving}
            style={{ color: editDirty ? C.accent : C.textDim, flexShrink: 0 }}
            onClick={saveFile}
          />
        </Tooltip>
        <span
          style={{
            color: C.text,
            fontSize: 13,
            fontWeight: 500,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
          }}
        >
          {editingFile.split('/').pop()}
          {editDirty && <span style={{ color: C.accent }}> ●</span>}
        </span>
        <Tooltip title="关闭文件">
          <Button
            type="text"
            size="small"
            icon={<CloseOutlined />}
            style={{ color: C.textSec }}
            onClick={() => closeFile()}
          />
        </Tooltip>
        <div style={{ width: 1, height: 16, background: C.border, flexShrink: 0 }} />
        <HeaderControls />
      </div>
      <textarea
        style={{
          flex: 1,
          background: C.bgDark,
          color: C.text,
          border: 'none',
          outline: 'none',
          padding: '14px 16px',
          fontSize: 13,
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
          lineHeight: 1.6,
          resize: 'none',
          width: '100%',
        }}
        value={editContent}
        onChange={(e) => setEditContent(e.target.value)}
        onKeyDown={handleKeyDown}
        spellCheck={false}
      />
    </div>
  );
}

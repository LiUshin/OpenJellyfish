import { type CSSProperties } from 'react';

export type SplitMode = 'chat' | 'split' | 'file';

interface Props {
  value: SplitMode;
  onChange: (mode: SplitMode) => void;
}

const MODES: SplitMode[] = ['chat', 'split', 'file'];
const LABELS = ['对话全屏', '分屏', '文件全屏'];

function ChatIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
      <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

function SplitIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
      <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
      <line x1="8" y1="3" x2="8" y2="13" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
      <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
      <line x1="4.5" y1="5.5" x2="11.5" y2="5.5" stroke="currentColor" strokeWidth="1" opacity="0.5" />
      <line x1="4.5" y1="8" x2="9.5" y2="8" stroke="currentColor" strokeWidth="1" opacity="0.5" />
      <line x1="4.5" y1="10.5" x2="10.5" y2="10.5" stroke="currentColor" strokeWidth="1" opacity="0.5" />
    </svg>
  );
}

const ICONS = [ChatIcon, SplitIcon, FileIcon];

const BTN_W = 28;
const GAP = 2;
const PAD = 2;

const containerStyle: CSSProperties = {
  display: 'inline-flex',
  position: 'relative',
  background: 'var(--jf-bg-deep)',
  borderRadius: 6,
  padding: PAD,
  gap: GAP,
  border: '1px solid var(--jf-border)',
};

const pillBase: CSSProperties = {
  position: 'absolute',
  top: PAD,
  width: BTN_W,
  height: BTN_W,
  borderRadius: 4,
  background: 'var(--jf-bg-raised)',
  boxShadow: '0 1px 3px rgba(0,0,0,0.25)',
  transition: 'left 0.28s cubic-bezier(0.34, 1.56, 0.64, 1)',
  zIndex: 0,
};

const btnBase: CSSProperties = {
  position: 'relative',
  zIndex: 1,
  border: 'none',
  background: 'transparent',
  cursor: 'pointer',
  width: BTN_W,
  height: BTN_W,
  borderRadius: 4,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  transition: 'color 0.2s',
  padding: 0,
};

export default function SplitToggle({ value, onChange }: Props) {
  const activeIdx = MODES.indexOf(value);
  const pillLeft = PAD + activeIdx * (BTN_W + GAP);

  return (
    <div style={containerStyle}>
      <div style={{ ...pillBase, left: pillLeft }} />
      {MODES.map((mode, i) => {
        const Icon = ICONS[i];
        return (
          <button
            key={mode}
            title={LABELS[i]}
            onClick={() => onChange(mode)}
            style={{
              ...btnBase,
              color: i === activeIdx ? 'var(--jf-primary)' : 'var(--jf-text-dim)',
            }}
          >
            <Icon />
          </button>
        );
      })}
    </div>
  );
}

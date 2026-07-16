import { useRef, useState, type DragEvent, type MouseEvent } from 'react';
import { Tooltip } from 'antd';
import { CloseOutlined } from '@ant-design/icons';
import type { FileTabSummary } from '../stores/fileWorkspaceContext';

const C = {
  bg: 'var(--jf-bg-panel)',
  bgDark: 'var(--jf-bg-deep)',
  text: 'var(--jf-text)',
  textSec: 'var(--jf-text-muted)',
  textDim: 'var(--jf-text-dim)',
  border: 'var(--jf-border)',
  accent: 'var(--jf-legacy)',
};

interface Props {
  tabs: FileTabSummary[];
  activePath: string | null;
  onActivate: (path: string) => void;
  onClose: (path: string) => void;
  onReorder: (fromIndex: number, toIndex: number) => void;
}

function tabLabel(path: string): string {
  return path.split('/').pop() || path;
}

/**
 * Chrome 风格文件 tab 条：
 * - 按打开顺序；HTML5 拖拽重排
 * - hover Tooltip 显示完整路径（不做内容小窗，避免 Office/大图解析）
 * - 点 X / 中键关闭单个 tab
 * - 溢出横向滚动；只维护 tab chrome，正文仍由 FilePreview 只渲 active
 */
export default function FileTabBar({
  tabs,
  activePath,
  onActivate,
  onClose,
  onReorder,
}: Props) {
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  if (tabs.length === 0) return null;

  const handleDragStart = (e: DragEvent, index: number) => {
    setDragFrom(index);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(index));
    // 避免拖拽时选中文字
    if (e.currentTarget instanceof HTMLElement) {
      e.currentTarget.style.opacity = '0.55';
    }
  };

  const handleDragEnd = (e: DragEvent) => {
    if (e.currentTarget instanceof HTMLElement) {
      e.currentTarget.style.opacity = '1';
    }
    setDragFrom(null);
    setDragOver(null);
  };

  const handleDragOver = (e: DragEvent, index: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (dragOver !== index) setDragOver(index);
  };

  const handleDrop = (e: DragEvent, toIndex: number) => {
    e.preventDefault();
    const raw = e.dataTransfer.getData('text/plain');
    const fromIndex = raw !== '' ? Number(raw) : dragFrom;
    setDragFrom(null);
    setDragOver(null);
    if (fromIndex == null || Number.isNaN(fromIndex)) return;
    onReorder(fromIndex, toIndex);
  };

  const handleCloseClick = (e: MouseEvent, path: string) => {
    e.stopPropagation();
    onClose(path);
  };

  return (
    <div
      ref={scrollerRef}
      role="tablist"
      style={{
        display: 'flex',
        alignItems: 'stretch',
        height: 34,
        flexShrink: 0,
        overflowX: 'auto',
        overflowY: 'hidden',
        background: C.bgDark,
        borderBottom: `1px solid ${C.border}`,
        scrollbarWidth: 'thin',
      }}
    >
      {tabs.map((tab, index) => {
        const active = tab.path === activePath;
        const label = tabLabel(tab.path);
        const isDropTarget = dragOver === index && dragFrom !== index;
        return (
          <Tooltip key={tab.path} title={tab.path} mouseEnterDelay={0.45} placement="bottom">
            <div
              role="tab"
              aria-selected={active}
              draggable
              onDragStart={(e) => handleDragStart(e, index)}
              onDragEnd={handleDragEnd}
              onDragOver={(e) => handleDragOver(e, index)}
              onDrop={(e) => handleDrop(e, index)}
              onClick={() => onActivate(tab.path)}
              onAuxClick={(e) => {
                // 中键关闭（Chrome 习惯）
                if (e.button === 1) {
                  e.preventDefault();
                  onClose(tab.path);
                }
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                maxWidth: 180,
                minWidth: 72,
                padding: '0 8px 0 10px',
                flexShrink: 0,
                cursor: 'pointer',
                userSelect: 'none',
                background: active ? C.bg : 'transparent',
                borderRight: `1px solid ${C.border}`,
                borderBottom: active ? `2px solid var(--jf-primary)` : '2px solid transparent',
                boxShadow: isDropTarget ? 'inset 2px 0 0 var(--jf-primary)' : undefined,
                color: active ? C.text : C.textSec,
                fontSize: 12,
                fontWeight: active ? 500 : 400,
              }}
            >
              <span
                style={{
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {label}
                {tab.dirty && (
                  <span style={{ color: C.accent, marginLeft: 3 }}>●</span>
                )}
              </span>
              <button
                type="button"
                aria-label={`关闭 ${label}`}
                onClick={(e) => handleCloseClick(e, tab.path)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 18,
                  height: 18,
                  border: 'none',
                  borderRadius: 3,
                  background: 'transparent',
                  color: C.textDim,
                  cursor: 'pointer',
                  flexShrink: 0,
                  padding: 0,
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'rgba(128,128,128,0.2)';
                  e.currentTarget.style.color = C.text;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.color = C.textDim;
                }}
              >
                <CloseOutlined style={{ fontSize: 10 }} />
              </button>
            </div>
          </Tooltip>
        );
      })}
    </div>
  );
}

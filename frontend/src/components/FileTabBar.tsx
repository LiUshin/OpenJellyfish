import { useEffect, useRef, useState, type CSSProperties, type DragEvent, type MouseEvent } from 'react';
import { Popover } from 'antd';
import { CloseOutlined } from '@ant-design/icons';
import type { FileTabSummary } from '../stores/fileWorkspaceContext';
import { getFileKind } from '../utils/fileKind';
import workspace from '../layouts/workspace.module.css';

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

function fileType(path: string): string {
  const name = tabLabel(path);
  const kind = getFileKind(name);
  if (kind === 'markdown') return 'MD';
  if (kind === 'image') return 'IMG';
  if (kind === 'audio') return 'AUD';
  if (kind === 'video') return 'VID';
  if (kind === 'text') {
    const extensionStart = name.lastIndexOf('.');
    return extensionStart > 0 ? name.slice(extensionStart + 1).toUpperCase().slice(0, 4) : 'TXT';
  }
  return kind.toUpperCase().slice(0, 4);
}

function shortName(path: string, maxLength: number): string {
  const name = tabLabel(path);
  const extensionStart = name.lastIndexOf('.');
  const characters = Array.from(extensionStart > 0 ? name.slice(0, extensionStart) : name);
  return characters.length > maxLength ? `${characters.slice(0, maxLength).join('')}…` : characters.join('');
}

function previewFallback(path: string): string {
  const kind = getFileKind(tabLabel(path));
  if (kind === 'image') return '图片文件，点击后在主预览区查看。';
  if (kind === 'pdf') return 'PDF 文件，点击后在主预览区查看。';
  if (kind === 'audio' || kind === 'video') return '媒体文件，点击后在主预览区查看。';
  if (kind === 'docx' || kind === 'xlsx' || kind === 'pptx') return 'Office 文件，点击后在主预览区查看。';
  return '此文件没有可用的文本预览，点击后查看。';
}

/** A compact file capsule in the preview toolbar. Hover expands its ordered ticks. */
export default function FileTabBar({ tabs, activePath, onActivate, onClose, onReorder }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [hoverPath, setHoverPath] = useState<string | null>(null);
  const [peekPath, setPeekPath] = useState<string | null>(null);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const collapseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoverPathRef = useRef<string | null>(null);

  const clearCollapse = () => {
    if (collapseTimer.current) clearTimeout(collapseTimer.current);
    collapseTimer.current = null;
  };
  const clearPreviewTimer = () => {
    if (previewTimer.current) clearTimeout(previewTimer.current);
    previewTimer.current = null;
  };
  const showHover = (path: string) => {
    if (hoverPathRef.current === path) return;
    hoverPathRef.current = path;
    setHoverPath(path);
    setPeekPath(null);
    clearPreviewTimer();
    previewTimer.current = setTimeout(() => setPeekPath(path), 310);
  };
  const clearHover = () => {
    clearPreviewTimer();
    hoverPathRef.current = null;
    setHoverPath(null);
    setPeekPath(null);
  };
  const expand = () => {
    clearCollapse();
    setExpanded(true);
  };
  const scheduleCollapse = () => {
    clearCollapse();
    clearPreviewTimer();
    collapseTimer.current = setTimeout(() => {
      setExpanded(false);
      clearHover();
    }, 300);
  };

  useEffect(() => () => {
    if (collapseTimer.current) clearTimeout(collapseTimer.current);
    if (previewTimer.current) clearTimeout(previewTimer.current);
  }, []);
  useEffect(() => {
    if (peekPath && !tabs.some(tab => tab.path === peekPath)) setPeekPath(null);
    if (hoverPath && !tabs.some(tab => tab.path === hoverPath)) {
      hoverPathRef.current = null;
      setHoverPath(null);
    }
  }, [peekPath, hoverPath, tabs]);

  if (tabs.length === 0) return null;

  const activeIndex = Math.max(0, tabs.findIndex(tab => tab.path === activePath));
  const activeTab = tabs[activeIndex];
  const highlightedPath = hoverPath ?? activeTab.path;
  const expandedWidth = Math.min(440, Math.max(194, 150 + tabs.length * 24));
  const leftMarks = Math.min(2, activeIndex);
  const rightMarks = Math.min(2, tabs.length - activeIndex - 1);

  const handleDragStart = (event: DragEvent, index: number) => {
    setDragFrom(index);
    clearHover();
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(index));
  };
  const handleDrop = (event: DragEvent, toIndex: number) => {
    if (dragFrom === null) return;
    event.preventDefault();
    setDragFrom(null);
    setDragOver(null);
    onReorder(dragFrom, toIndex);
  };
  const handleCloseClick = (event: MouseEvent, path: string) => {
    event.stopPropagation();
    clearHover();
    onClose(path);
  };

  return (
    <div
      className={workspace.fileTabDock}
      data-jf-file-tab-capsule
      data-expanded={expanded}
      onMouseEnter={expand}
      onMouseLeave={scheduleCollapse}
      onFocusCapture={clearCollapse}
      onBlurCapture={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) scheduleCollapse();
      }}
    >
      <div
        className={[workspace.fileTabsRail, expanded ? workspace.fileTabsRailExpanded : ''].filter(Boolean).join(' ')}
        style={{ '--file-expanded-width': `${expandedWidth}px` } as CSSProperties}
      >
        <button
          type="button"
          className={workspace.fileTabCollapsed}
          aria-label={`打开的文件：${tabLabel(activeTab.path)}，共 ${tabs.length} 个；展开文件标签`}
          aria-expanded={expanded}
          tabIndex={expanded ? -1 : 0}
          onClick={() => {
            expand();
            requestAnimationFrame(() => scrollerRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus());
          }}
        >
          <span className={workspace.fileTabCollapsedTicks} aria-hidden="true">
            {Array.from({ length: leftMarks }, (_, index) => <i key={index} />)}
          </span>
          <span className={workspace.fileTabCollapsedChip}>
            <span className={workspace.fileTabType}>{fileType(activeTab.path)}</span>
            <span className={workspace.fileTabCollapsedName}>{shortName(activeTab.path, 4)}</span>
          </span>
          <span className={workspace.fileTabCollapsedTicks} aria-hidden="true">
            {Array.from({ length: rightMarks }, (_, index) => <i key={index} />)}
          </span>
        </button>
        <div
          ref={scrollerRef}
          role="tablist"
          aria-label="打开的文件"
          aria-orientation="horizontal"
          aria-hidden={!expanded}
          className={workspace.fileTabsScroller}
          onPointerMove={event => {
            // A growing chip can move another tick under a stationary pointer.
            // Only real pointer movement may select a different tick.
            if (!expanded || dragFrom !== null || (event.movementX === 0 && event.movementY === 0)) return;
            const slot = event.target instanceof Element
              ? event.target.closest<HTMLElement>('[data-file-tab-path]') : null;
            if (slot && scrollerRef.current?.contains(slot)) showHover(slot.dataset.fileTabPath!);
          }}
        >
          <div className={workspace.fileTabsList}>
            {tabs.map((tab, index) => {
              const active = tab.path === activePath;
              const highlighted = tab.path === highlightedPath;
              const label = tabLabel(tab.path);
              const card = (
                <div className={workspace.fileTabCard} onMouseEnter={expand} onMouseLeave={scheduleCollapse}>
                  <div className={workspace.fileTabCardHeader}><strong title={tab.title}>{tab.title}</strong></div>
                  <div className={workspace.fileTabCardPath} title={tab.path}>{tab.path}</div>
                  <div className={workspace.fileTabCardDivider} />
                  <div className={workspace.fileTabCardCaption}>内容预览</div>
                  <div className={workspace.fileTabCardPreview}>{tab.previewText ?? previewFallback(tab.path)}</div>
                  {tab.dirty && <div className={workspace.fileTabCardDirty}>有未保存的更改</div>}
                </div>
              );
              return (
                <Popover
                  key={tab.path}
                  content={card}
                  placement="bottomLeft"
                  overlayClassName={workspace.fileTabPopover}
                  trigger={[]}
                  open={expanded && dragFrom === null && peekPath === tab.path}
                >
                  <div
                    data-file-tab-path={tab.path}
                    className={[
                      workspace.fileTabSlot,
                      highlighted ? workspace.fileTabSlotHighlight : '',
                      active ? workspace.fileTabActive : '',
                      dragOver === index && dragFrom !== index ? workspace.fileTabDropTarget : '',
                    ].filter(Boolean).join(' ')}
                    draggable
                    onDragStart={event => handleDragStart(event, index)}
                    onDragEnd={() => { setDragFrom(null); setDragOver(null); }}
                    onDragOver={event => {
                      if (dragFrom === null) return;
                      event.preventDefault();
                      event.dataTransfer.dropEffect = 'move';
                      if (dragOver !== index) setDragOver(index);
                    }}
                    onDrop={event => handleDrop(event, index)}
                  >
                    <div
                      role="tab"
                      className={workspace.fileTab}
                      tabIndex={expanded && active ? 0 : -1}
                      aria-selected={active}
                      aria-label={tab.path + (tab.dirty ? '，未保存' : '')}
                      onFocus={() => showHover(tab.path)}
                      onKeyDown={event => {
                        if (event.target !== event.currentTarget) return;
                        const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
                          : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
                          : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
                        if (next !== null) {
                          event.preventDefault();
                          showHover(tabs[next].path);
                          scrollerRef.current?.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus();
                        } else if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          clearHover();
                          onActivate(tab.path);
                        } else if (event.key === 'Escape') {
                          event.preventDefault();
                          clearHover();
                          setExpanded(false);
                        } else if (event.key === 'Delete' || event.key === 'Backspace') {
                          event.preventDefault();
                          clearHover();
                          onClose(tab.path);
                        }
                      }}
                      onClick={() => {
                        clearHover();
                        onActivate(tab.path);
                      }}
                      onAuxClick={event => {
                        if (event.button === 1) {
                          event.preventDefault();
                          clearHover();
                          onClose(tab.path);
                        }
                      }}
                    >
                      <span className={workspace.fileTabMark} aria-hidden="true" />
                      <span className={workspace.fileTabLabel}>{fileType(tab.path)} · {shortName(tab.path, 7)}</span>
                      {tab.dirty && <span className={workspace.fileTabDirty} aria-hidden="true" />}
                    </div>
                    <button
                      type="button"
                      className={workspace.fileTabClose}
                      aria-label={`关闭 ${label}`}
                      tabIndex={expanded && highlighted ? 0 : -1}
                      onClick={event => handleCloseClick(event, tab.path)}
                    >
                      <CloseOutlined aria-hidden="true" />
                    </button>
                  </div>
                </Popover>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

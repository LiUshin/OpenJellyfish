import { memo, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { truncatePreview } from '../utils/userQueryPreview';
import styles from './queryNav.module.css';

export interface QueryNavigationItem { id: string; question: string; answer: string }
const ROW_HEIGHT = 24;
const OVERSCAN = 4;

function QueryNavigation({ items, activeId, onJump }: {
  items: QueryNavigationItem[]; activeId: string; onJump: (id: string) => void;
}) {
  const rail = useRef<HTMLElement>(null);
  const pointerInside = useRef(false);
  const focusInside = useRef(false);
  const pendingFocus = useRef<number | null>(null);
  const [top, setTop] = useState(0);
  const [height, setHeight] = useState(360);
  const [preview, setPreview] = useState<{ id: string; top: number; left: number } | null>(null);
  const tooltipId = useId();
  const activeIndex = items.findIndex(item => item.id === activeId);
  const hoverIndex = preview ? items.findIndex(item => item.id === preview.id) : -1;
  const first = Math.max(0, Math.floor(top / ROW_HEIGHT) - OVERSCAN);
  const last = Math.min(items.length, Math.ceil((top + height) / ROW_HEIGHT) + OVERSCAN);

  useEffect(() => {
    const el = rail.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setHeight(el.clientHeight));
    observer.observe(el);
    const dismiss = () => setPreview(null);
    window.addEventListener('resize', dismiss);
    return () => { observer.disconnect(); window.removeEventListener('resize', dismiss); };
  }, []);
  useEffect(() => {
    const el = rail.current;
    if (!el || activeIndex < 0 || pointerInside.current || focusInside.current) return;
    const y = activeIndex * ROW_HEIGHT;
    if (y < el.scrollTop || y + ROW_HEIGHT > el.scrollTop + el.clientHeight) el.scrollTop = Math.max(0, y - el.clientHeight / 2 + ROW_HEIGHT / 2);
    setTop(el.scrollTop);
  }, [activeIndex, height]);
  useLayoutEffect(() => {
    if (pendingFocus.current === null) return;
    const button = rail.current?.querySelector<HTMLButtonElement>(`[data-query-position="${pendingFocus.current}"]`);
    if (button) { pendingFocus.current = null; button.focus({ preventScroll: true }); }
  });

  function show(index: number, element: HTMLButtonElement) {
    if (!items[index]) return;
    const rect = element.getBoundingClientRect();
    const width = Math.min(300, window.innerWidth - 24);
    setPreview({ id: items[index].id, left: Math.max(12, Math.min(rect.right + 10, window.innerWidth - width - 12)), top: Math.max(12, Math.min(rect.top - 65, window.innerHeight - 260)) });
  }
  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === 'Escape') { setPreview(null); event.stopPropagation(); return; }
    const next = event.key === 'ArrowDown' ? Math.min(items.length - 1, index + 1)
      : event.key === 'ArrowUp' ? Math.max(0, index - 1)
      : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : null;
    if (next === null) return;
    event.preventDefault(); event.stopPropagation();
    const el = rail.current;
    if (!el) return;
    pendingFocus.current = next;
    const y = next * ROW_HEIGHT;
    if (y < el.scrollTop) el.scrollTop = y;
    else if (y + ROW_HEIGHT > el.scrollTop + el.clientHeight) el.scrollTop = y + ROW_HEIGHT - el.clientHeight;
    setTop(el.scrollTop); setPreview(null);
    // State can be unchanged when both rows are already visible.
    const button = el.querySelector<HTMLButtonElement>(`[data-query-position="${next}"]`);
    if (button) { pendingFocus.current = null; button.focus({ preventScroll: true }); }
  }
  const selected = hoverIndex >= 0 ? items[hoverIndex] : null;
  return <>
    <nav ref={rail} className={styles.rail} aria-label="对话问答导航"
      onPointerEnter={() => { pointerInside.current = true; }}
      onPointerLeave={() => { pointerInside.current = false; if (!focusInside.current) setPreview(null); }}
      onFocus={() => { focusInside.current = true; }}
      onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) { focusInside.current = false; setPreview(null); } }}
      onScroll={e => {
        setTop(e.currentTarget.scrollTop);
        const focused = document.activeElement as HTMLButtonElement | null;
        const position = focused?.dataset.queryPosition;
        if (focusInside.current && position != null && e.currentTarget.contains(focused)) show(Number(position), focused!);
        else setPreview(null);
      }}>
      <div style={{ height: items.length * ROW_HEIGHT, position: 'relative' }}>
        {items.slice(first, last).map((item, offset) => {
          const index = first + offset;
          const active = item.id === activeId;
          const width = Math.max(active ? 16 : 9, hoverIndex < 0 ? 0 : 30 - Math.abs(hoverIndex - index) * 6);
          return <button key={item.id} type="button" className={`${styles.marker} ${active ? styles.markerActive : ''}`}
            style={{ top: index * ROW_HEIGHT, height: ROW_HEIGHT }} data-query-position={index}
            aria-label={`第 ${index + 1} 条：${truncatePreview(item.question, 80) || '附件消息'}`}
            aria-current={active ? 'true' : undefined} aria-describedby={preview?.id === item.id ? tooltipId : undefined}
            onPointerEnter={e => show(index, e.currentTarget)} onFocus={e => show(index, e.currentTarget)}
            onKeyDown={e => navigate(e, index)} onClick={() => onJump(item.id)}>
            <span className={styles.dash} style={{ width }} aria-hidden="true" />
          </button>;
        })}
      </div>
    </nav>
    {selected && preview && createPortal(<div id={tooltipId} role="tooltip" className={styles.preview} style={{ left: preview.left, top: preview.top }}>
      <div className={styles.previewMeta}>对话定位 <span>{hoverIndex + 1} / {items.length}</span></div>
      <div className={styles.question}>{truncatePreview(selected.question, 80) || '附件消息'}</div>
      <div className={styles.answer}>{truncatePreview(selected.answer, 120) || '本轮尚无正文回答'}</div>
      <div className={styles.hint}>点击跳转至这一轮</div>
    </div>, document.body)}
  </>;
}
export default memo(QueryNavigation);

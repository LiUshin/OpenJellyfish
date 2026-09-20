import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { CaretDown, CheckCircle, PauseCircle, WarningCircle, ArrowDown } from '@phosphor-icons/react';
import { responsePhase } from '../utils/responsePresentation';
import styles from './workProgress.module.css';

export function PixelLoader() {
  return <span className={styles.pixels} aria-hidden="true">{Array.from({ length: 25 }, (_, i) =>
    <i key={i} style={{ animationDelay: `${-((i * 7) % 25) * 55}ms` }} />)}</span>;
}

function Elapsed({ startedAt, finishedAt, active }: { startedAt: number; finishedAt?: number; active: boolean }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [active]);
  // A terminal event may arrive before the durable end timestamp. Don't keep
  // ticking or invent a completion time in that brief interval.
  if (!active && !finishedAt) return null;
  const seconds = Math.max(0, Math.floor((finishedAt ?? now) - startedAt));
  return <span className={styles.elapsed} title="本轮经过时间，包含排队和等待确认">{seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`}</span>;
}

export default function WorkProgress({ status, label, count, startedAt, finishedAt, children }: {
  status: string; label: string; count: number; startedAt?: number; finishedAt?: number; children: ReactNode;
}) {
  const phase = responsePhase(status);
  const [disclosure, setDisclosure] = useState({ phase, open: phase === 'working' });
  // Reset on a phase transition only. New tool events must not override a
  // user's decision to collapse the current working phase.
  if (disclosure.phase !== phase) setDisclosure({ phase, open: phase === 'working' });
  const open = disclosure.phase === phase ? disclosure.open : phase === 'working';
  const id = useId();
  const visited = useRef(open);
  if (open) visited.current = true;
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const [away, setAway] = useState(false);
  useEffect(() => {
    const box = viewport.current, body = content.current;
    if (!box || !body || !open) return;
    const update = () => {
      if (following.current) box.scrollTop = box.scrollHeight;
      setAway(!following.current && box.scrollHeight - box.scrollTop - box.clientHeight > 20);
    };
    const observer = new ResizeObserver(update);
    observer.observe(box); observer.observe(body); update();
    return () => observer.disconnect();
  }, [open]);
  const pause = () => { following.current = false; };
  const working = phase === 'working';
  return <section className={styles.work} data-work-phase={phase}>
    <button type="button" className={styles.summary} aria-expanded={open} aria-controls={id}
      onClick={() => setDisclosure({ phase, open: !open })}>
      {working ? <PixelLoader /> : status === 'failed' ? <WarningCircle size={18} /> : phase === 'waiting' || status === 'cancelled' ? <PauseCircle size={18} /> : <CheckCircle size={18} />}
      <span className={styles.summaryLabel}>{label}</span>
      {count > 0 && <span className={styles.count}>{count} 项活动</span>}
      {startedAt != null && <Elapsed startedAt={startedAt} finishedAt={finishedAt} active={phase !== 'settled'} />}
      <CaretDown size={13} className={open ? styles.caretOpen : styles.caret} />
    </button>
    <div id={id} hidden={!open}>
      <div ref={viewport} className={styles.viewport} tabIndex={0} aria-label="本轮执行进展"
        onWheel={e => { e.stopPropagation(); if (e.deltaY < 0) pause(); }}
        onKeyDown={e => { if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(e.key)) { e.stopPropagation(); pause(); } }}
        onClickCapture={e => { if ((e.target as HTMLElement).closest('button, summary')) pause(); }}
        onScroll={e => {
          const box = e.currentTarget;
          following.current = box.scrollHeight - box.scrollTop - box.clientHeight < 20;
          setAway(!following.current);
        }}>
        <div ref={content} className={styles.timeline}>{visited.current && children}</div>
      </div>
      {away && <button type="button" className={styles.latest} onClick={() => {
        following.current = true; setAway(false);
        if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
      }}><ArrowDown size={12} />回到最新进展</button>}
    </div>
  </section>;
}

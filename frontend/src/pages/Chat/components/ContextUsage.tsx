import { useId, useRef, useState } from 'react';
import { Popover, Tooltip } from 'antd';
import { ArrowUpRight } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { contextCapacity, estimateTextTokens, type ContextUsageSnapshot, type ConversationTextUsage } from '../utils/contextUsage';
import styles from './contextUsage.module.css';

const format = (value: number) => value >= 1_000_000 ? `${+(value / 1_000_000).toFixed(2)}M`
  : value >= 1000 ? `${+(value / 1000).toFixed(1)}k` : String(value);

export interface ContextUsageProps {
  textUsage: ConversationTextUsage;
  snapshot?: ContextUsageSnapshot | null;
  draft?: string;
  draftAttachments?: number;
}

/** Window occupancy and visible-text estimates are deliberately separate. */
export default function ContextUsage({ textUsage, snapshot, draft = '', draftAttachments = 0 }: ContextUsageProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const { used, limit, free, percent } = contextCapacity(snapshot);
  const draftTokens = estimateTextTokens(draft);
  const estimate = textUsage.messages + textUsage.tools + draftTokens;
  const ratio = percent === null ? 0 : Math.min(100, percent);
  const rows = [
    { key: 'messages', value: textUsage.messages, color: styles.messages, detail: t('context.messagesDetail', { count: textUsage.messageCount }) },
    { key: 'tools', value: textUsage.tools, color: styles.tools, detail: t('context.toolsDetail', { count: textUsage.toolCount }) },
    { key: 'draft', value: draftTokens, color: styles.draft, detail: t('context.draftDetail') },
  ];
  const panel = <section id={panelId} className={styles.panel} aria-label={t('context.title')}
    onKeyDown={event => { if (event.key === 'Escape') { setOpen(false); triggerRef.current?.focus(); event.stopPropagation(); } }}>
    <div className={styles.section}>
      <div className={styles.heading}>
        <span>{t('context.title')} <strong>{percent === null ? '—' : `${percent}%`}</strong></span>
        <span className={styles.muted}>{used === null ? '—' : format(used)} <span className={styles.of}>/</span> {limit === null ? '—' : format(limit)}</span>
      </div>
      <div className={styles.track} role={percent === null ? undefined : 'progressbar'}
        aria-label={t('context.title')} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={percent === null ? undefined : ratio} aria-valuetext={percent === null ? undefined : `${percent}%`}>
        {percent !== null && <span className={styles.usedFill} style={{ width: `${ratio}%` }} />}
      </div>
      <div className={styles.capacityLegend}>
        <span><i className={styles.messages} />{t('context.used')} <b>{used === null ? '—' : format(used)}</b></span>
        <span><i className={styles.free} />{t('context.free')} <b>{free === null ? '—' : format(free)}</b></span>
      </div>
      <p className={styles.note}>{t(used === null ? 'context.notReported' : 'context.lastSnapshot')}</p>
    </div>
    <div className={styles.section}>
      <div className={styles.subheading}><span>{t('context.visibleText')}</span><span>{t('context.estimate')} · ~{format(estimate)}</span></div>
      <div className={styles.track} aria-hidden="true">
        {estimate > 0 && rows.map(row => row.value > 0 ? <span key={row.key} className={row.color} style={{ width: `${row.value / estimate * 100}%` }} /> : null)}
      </div>
      <div className={styles.legend}>
        {rows.map(row => <Tooltip key={row.key} trigger={['hover', 'focus']} title={<div className={styles.detail}><strong>{t(`context.${row.key}`)} · ~{format(row.value)}</strong><span>{row.detail}</span></div>}>
          <button type="button" className={styles.legendItem} aria-label={`${t(`context.${row.key}`)}: ~${format(row.value)}. ${row.detail}`}>
            <i className={row.color} /><span>{t(`context.${row.key}`)}</span><b>~{format(row.value)}</b>
          </button>
        </Tooltip>)}
        <Tooltip trigger={['hover', 'focus']} title={t('context.attachmentsDetail')}><button type="button" className={styles.legendItem}>
          <i className={styles.attachments} /><span>{t('context.attachments')}</span><b>{textUsage.attachmentCount + draftAttachments}</b>
        </button></Tooltip>
      </div>
      <p className={styles.note}>{t('context.estimateNote')}</p>
    </div>
    <div className={styles.footer}>
      <span>{t('context.footerHint')}</span>
      <button type="button" onClick={() => { setOpen(false); navigate('/settings/usage'); }}>{t('context.viewUsage')}<ArrowUpRight size={13} /></button>
    </div>
  </section>;
  return <Popover open={open} onOpenChange={setOpen} content={panel} placement="top"
    trigger="click" arrow={false} overlayClassName={styles.popover}>
    <button type="button" ref={triggerRef} className={`${styles.trigger} ${percent !== null && percent >= 90 ? styles.warning : ''}`}
      aria-label={`${t('context.title')}${percent === null ? '' : ` ${percent}%`}`}
      aria-expanded={open} aria-controls={open ? panelId : undefined}
      onKeyDown={event => { if (event.key === 'Escape') { setOpen(false); event.stopPropagation(); } }}>
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
        <circle className={styles.ringTrack} cx="12" cy="12" r="8" fill="none" strokeWidth="3" strokeDasharray={percent === null ? '2 3' : undefined} />
        {percent !== null && <circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"
          strokeDasharray={`${ratio / 100 * 50.265} 50.265`} transform="rotate(-90 12 12)" />}
      </svg>
    </button>
  </Popover>;
}

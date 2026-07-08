import { memo } from 'react';
import { Input, Tooltip } from 'antd';
import { ArrowElbowDownLeft, X, CaretUp, CaretDown } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import type { QueryQueueItem } from '../types/queryQueue';
import styles from '../chat.module.css';

interface QueryQueuePanelProps {
  items: QueryQueueItem[];
  onChange: (items: QueryQueueItem[]) => void;
  onRemove: (id: string) => void;
  /** 中断立刻执行：停止当前执行并运行此条。 */
  onRunInterrupt?: (item: QueryQueueItem) => void;
  /** 当前会话是否正在流式运行；false = 空闲（仅按序排队执行），中断按钮禁用。 */
  canInterrupt?: boolean;
  /** HITL pending — 中断禁用。 */
  hitlLocked?: boolean;
}

function moveItem(items: QueryQueueItem[], from: number, to: number): QueryQueueItem[] {
  if (to < 0 || to >= items.length) return items;
  const next = [...items];
  const [row] = next.splice(from, 1);
  next.splice(to, 0, row);
  return next;
}

function QueryQueuePanel({
  items,
  onChange,
  onRemove,
  onRunInterrupt,
  canInterrupt = false,
  hitlLocked = false,
}: QueryQueuePanelProps) {
  const { t } = useTranslation();

  if (items.length === 0) return null;

  const interruptEnabled = canInterrupt && !hitlLocked;
  const interruptHint = interruptEnabled ? undefined : t('chat.queryInterruptIdle');

  return (
    <ul className={styles.queryQueueList}>
      {items.map((item, idx) => (
        <li key={item.id} className={styles.queryQueueRow}>
          <Input.TextArea
            className={styles.queryQueueInput}
            value={item.content}
            onChange={(e) => onChange(items.map((it) => (it.id === item.id ? { ...it, content: e.target.value } : it)))}
            autoSize={{ minRows: 1, maxRows: 4 }}
            variant="borderless"
            placeholder={t('chat.queryQueuePlaceholder')}
          />
          <div className={styles.queryQueuePrimary}>
            <Tooltip title={interruptHint ?? t('chat.queryInterruptBtn')}>
              <button
                type="button"
                className={`${styles.queryQueuePrimaryBtn} ${styles.queryInterruptBtn}`}
                disabled={!interruptEnabled || !onRunInterrupt}
                onClick={() => onRunInterrupt?.(item)}
                aria-label={t('chat.queryInterruptBtn')}
              >
                <ArrowElbowDownLeft size={13} weight="bold" />
              </button>
            </Tooltip>
          </div>
          <div className={styles.queryQueueActions}>
            <button
              type="button"
              className={styles.queryQueueActBtn}
              disabled={idx === 0}
              onClick={() => onChange(moveItem(items, idx, idx - 1))}
              aria-label={t('chat.queryMoveUp')}
            >
              <CaretUp size={12} />
            </button>
            <button
              type="button"
              className={styles.queryQueueActBtn}
              disabled={idx === items.length - 1}
              onClick={() => onChange(moveItem(items, idx, idx + 1))}
              aria-label={t('chat.queryMoveDown')}
            >
              <CaretDown size={12} />
            </button>
            <button
              type="button"
              className={`${styles.queryQueueActBtn} ${styles.queryQueueRemoveBtn}`}
              onClick={() => onRemove(item.id)}
              aria-label={t('chat.queryRemove')}
            >
              <X size={12} />
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

export default memo(QueryQueuePanel);

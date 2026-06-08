/**
 * ConversationDrawer — 消费者侧「我的会话」抽屉。
 *
 * 会话列表存在 localStorage（本浏览器维度），点击切换会从后端按 conv_id 拉历史。
 * 删除为本地移除（不动服务器数据）。从左侧滑出，移动端友好。
 */

import { useTranslation } from 'react-i18next';
import type { ConvMeta } from './serviceApi';
import styles from './serviceChat.module.css';

interface Props {
  items: ConvMeta[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}

function fmtTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString([], { month: '2-digit', day: '2-digit' });
}

export default function ConversationDrawer({
  items,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onClose,
}: Props) {
  const { t } = useTranslation();

  return (
    <div className={styles.convOverlay} onClick={onClose}>
      <div className={styles.convDrawer} onClick={(e) => e.stopPropagation()}>
        <div className={styles.convDrawerHeader}>
          <span className={styles.convDrawerTitle}>{t('service.convListTitle', '我的会话')}</span>
          <button
            type="button"
            className={styles.convDrawerClose}
            onClick={onClose}
            aria-label={t('service.filesClose', '关闭')}
          >
            ×
          </button>
        </div>

        <button type="button" className={styles.convNewBtn} onClick={onNew}>
          ＋ {t('service.convNew', '新建对话')}
        </button>

        <div className={styles.convList}>
          {items.length === 0 && (
            <div className={styles.convEmpty}>{t('service.convEmpty', '还没有会话')}</div>
          )}
          {items.map((c) => (
            <div
              key={c.id}
              className={`${styles.convRow} ${c.id === activeId ? styles.convRowActive : ''}`}
              onClick={() => onSelect(c.id)}
            >
              <div className={styles.convRowMain}>
                <div className={styles.convRowTitle}>
                  {c.title || t('service.convUntitled', '新对话')}
                </div>
                <div className={styles.convRowTime}>{fmtTime(c.updatedAt)}</div>
              </div>
              <button
                type="button"
                className={styles.convRowDelete}
                title={t('service.convDelete', '移除')}
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(c.id);
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

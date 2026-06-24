import { truncatePreview } from '../utils/userQueryPreview';
import styles from './queryNav.module.css';

interface QueryNavMarkerProps {
  /** 完整用户 query 文本（组件内截断至 100 字用于 hover 预览）。 */
  preview: string;
  /** 当前视口正在阅读这条 query 时高亮（active 态）。 */
  active?: boolean;
  onClick: () => void;
}

/**
 * 用户 query 左侧导航标记：默认短横线，hover 略放大并展示 query 摘要；
 * active（滚动到对应 query）时常驻高亮。
 */
export function QueryNavMarker({ preview, active = false, onClick }: QueryNavMarkerProps) {
  const label = truncatePreview(preview, 100);

  return (
    <button
      type="button"
      className={`${styles.marker}${active ? ` ${styles.markerActive}` : ''}`}
      onClick={onClick}
      aria-label={label || 'Jump to message'}
      aria-current={active ? 'true' : undefined}
      title={label}
    >
      <span className={styles.dash} aria-hidden />
      {label ? (
        <span className={styles.preview} role="tooltip">
          {label}
        </span>
      ) : null}
    </button>
  );
}

export default QueryNavMarker;

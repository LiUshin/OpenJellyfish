import { useEffect, useMemo, useRef } from 'react';
import { File as FileIcon, Folder as FolderIcon } from '@phosphor-icons/react';
import type { FuzzyResult } from '../../../utils/fuzzyMatch';
import { fuzzyMatch, highlightMatches } from '../../../utils/fuzzyMatch';
import type { FileIndexEntry } from '../../../services/api';
import styles from '../chat.module.css';

interface MentionPickerProps {
  /** Raw query string after the leading `@` (without the `@` itself). */
  query: string;
  /** Full flattened file index from /api/files/index. */
  items: FileIndexEntry[];
  /** Recently opened paths (most-recent-first). */
  recentPaths: string[];
  /** Currently selected index in the candidate list. */
  activeIndex: number;
  onActiveIndexChange: (idx: number) => void;
  onSelect: (item: FileIndexEntry) => void;
  /** When true, render. When false, parent should unmount instead of passing
   *  visible={false}, so we don't have to track mount/unmount inside picker. */
  visible: boolean;
}

const MAX_CANDIDATES = 8;

/**
 * Dropdown list shown above the chat input when the user types `@<query>`.
 *
 * - Pure presentational; parent owns query/active-index state.
 * - Uses `fuzzyMatch` from `utils/fuzzyMatch` for ranking.
 * - Keyboard handling lives in parent (Chat/index.tsx) so it can intercept
 *   ↑↓/Enter/Tab/Esc on the textarea before they fire onChange/handleSend.
 */
export default function MentionPicker({
  query,
  items,
  recentPaths,
  activeIndex,
  onActiveIndexChange,
  onSelect,
  visible,
}: MentionPickerProps) {
  const candidates: FuzzyResult<FileIndexEntry>[] = useMemo(
    () => fuzzyMatch(items, query, recentPaths, MAX_CANDIDATES),
    [items, query, recentPaths],
  );

  const listRef = useRef<HTMLDivElement>(null);

  // Keep active item scrolled into view when it changes (keyboard nav).
  useEffect(() => {
    if (!visible) return;
    const el = listRef.current?.children[activeIndex] as HTMLElement | undefined;
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest' });
    }
  }, [activeIndex, visible]);

  if (!visible) return null;
  if (candidates.length === 0) {
    return (
      <div className={styles.mentionPicker}>
        <div className={styles.mentionEmpty}>没有匹配的文件</div>
      </div>
    );
  }

  return (
    <div className={styles.mentionPicker} role="listbox">
      <div className={styles.mentionHeader}>
        <span>选择文件 / 文件夹</span>
        <span className={styles.mentionHint}>↑↓ 选择 · Enter / Tab 插入 · Esc 取消</span>
      </div>
      <div ref={listRef} className={styles.mentionList}>
        {candidates.map((c, idx) => {
          const segments = highlightMatches(c.item.name, c.nameMatches);
          const dirHint = c.item.path.replace(/\/[^/]+\/?$/, '/') || '/';
          return (
            <div
              key={c.item.path}
              className={`${styles.mentionItem} ${idx === activeIndex ? styles.mentionItemActive : ''}`}
              role="option"
              aria-selected={idx === activeIndex}
              onMouseEnter={() => onActiveIndexChange(idx)}
              onMouseDown={(e) => {
                // mousedown not click — click would fire after textarea blur,
                // which would close the picker before the select handler runs.
                e.preventDefault();
                onSelect(c.item);
              }}
            >
              <span className={styles.mentionIcon}>
                {c.item.is_dir ? <FolderIcon size={14} weight="fill" /> : <FileIcon size={14} />}
              </span>
              <span className={styles.mentionName}>
                {segments.map((seg, i) => (
                  <span
                    key={i}
                    className={seg.highlight ? styles.mentionNameMatch : ''}
                  >{seg.text}</span>
                ))}
                {c.item.is_dir && '/'}
              </span>
              <span className={styles.mentionDir}>{dirHint}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export { MAX_CANDIDATES };

import { FileCode } from '@phosphor-icons/react';
import type { ToolBlock } from '../types';
import { lineDiff } from '../../../utils/unifiedDiff';
import { toolOutcome } from '../utils/responsePresentation';
import { DiffLineRow } from './EditDiffViewer';
import styles from '../chat.module.css';

export default function FileChangeCard({ block }: { block: ToolBlock }) {
  const outcome = toolOutcome(block);
  const label = { running: '正在修改', done: '已修改', failed: '修改失败', stopped: '已停止', unknown: '状态未确认' }[outcome];
  return <>{block.changes?.map((change, index) => <section className={styles.streamFileCard} key={`${change.path}-${index}`} aria-label={`文件修改 ${change.path}`}>
    <div className={styles.streamFileHeader}>
      <div className={styles.streamFileHeaderLeft}><FileCode size={16} />
        {change.preview_path ? <button type="button" className={styles.filePathButton} data-jf-file={change.preview_path} title="在右侧预览文件">{change.workspace_path || change.path}</button> : <span className={styles.streamFilePath}>{change.path}</span>}
      </div><span className={styles.streamFileStatus}>{label}</span>
    </div>
    <div className={styles.diffBody}>
      {change.diff != null ? change.diff.split('\n').map((line, i) =>
        line.startsWith('@@') || /^(diff |index |--- |\+\+\+ )/.test(line)
          ? <div className={styles.diffHunkHeader} key={i}>{line}</div>
          : <DiffLineRow key={i} line={{ oldNum: null, newNum: null, type: line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : 'context', text: /^[+ -]/.test(line) ? line.slice(1) : line }} />)
        : change.old_text != null && change.new_text != null
          ? lineDiff(change.old_text.split('\n'), change.new_text.split('\n')).map((line, i) => <DiffLineRow key={i} line={line} />)
          : <div className={styles.diffEmpty}>客户端未提供修改差异</div>}
    </div>
  </section>)}</>;
}

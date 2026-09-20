import { memo, useId, useState } from 'react';
import { CaretDown, CircleNotch, CheckCircle, WarningCircle, MinusCircle } from '@phosphor-icons/react';
import type { ToolBlock } from '../types';
import { toolLabel, toolOutcome } from '../utils/responsePresentation';
import styles from './workProgress.module.css';

function ToolIndicator({ block }: { block: ToolBlock }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const outcome = toolOutcome(block);
  const label = { running: '进行中', done: '已完成', failed: '失败', stopped: block.status === 'declined' || block.result === '已拒绝' ? '已拒绝' : '已取消', unknown: '状态未确认' }[outcome];
  const Icon = outcome === 'running' ? CircleNotch : outcome === 'failed' ? WarningCircle : outcome === 'done' ? CheckCircle : MinusCircle;
  return <div className={styles.tool} data-outcome={outcome}>
    <button type="button" className={styles.toolButton} aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(!expanded)}>
      <Icon size={14} /> <span className={styles.toolName}>{toolLabel(block.name)}</span>
      <span className={styles.toolState}>{label}</span><CaretDown size={12} className={expanded ? styles.caretOpen : styles.caret} />
    </button>
    {expanded && <div className={styles.toolDetails} id={id}>
      <code>{block.name}</code>
      {block.exit_code != null && <p>退出码：{block.exit_code}</p>}
      {block.args.trim() && <><h5>输入</h5><pre>{block.args}</pre></>}
      {block.result.trim() && <><h5>结果</h5>{['已完成', '失败', '已取消', '已拒绝'].includes(block.result) ? <p>仅有执行状态；此记录未包含详细结果。</p> : <pre>{block.result}</pre>}</>}
      {!block.args.trim() && !block.result.trim() && <p>暂无调用详情</p>}
    </div>}
  </div>;
}
export default memo(ToolIndicator);

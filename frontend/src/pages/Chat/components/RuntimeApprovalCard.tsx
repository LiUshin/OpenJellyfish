import { useMemo } from 'react';
import { Button, Popconfirm } from 'antd';
import { Check, FileCode, ShieldCheck, X } from '@phosphor-icons/react';
import type { RuntimeApproval } from '../../../services/runtime';
import { lineDiff } from '../../../utils/unifiedDiff';
import { DiffLineRow } from './EditDiffViewer';
import styles from './runtimeApproval.module.css';

type ApprovalChange = NonNullable<RuntimeApproval['changes']>[number] & {
  old_text?: string;
  new_text?: string;
};

function FilePreview({ change }: { change: ApprovalChange }) {
  const pairedText = typeof change.old_text === 'string' && typeof change.new_text === 'string';
  const textDiff = useMemo(() => {
    if (typeof change.old_text !== 'string' || typeof change.new_text !== 'string') return null;
    const before = change.old_text.split('\n');
    const after = change.new_text.split('\n');
    // lineDiff builds an O(N*M) table. A long source file must not freeze the
    // approval controls while the user decides whether to allow the change.
    if (before.length * after.length > 1_000_000) return null;
    return lineDiff(before, after);
  }, [change.old_text, change.new_text]);
  const diff = change.diff || '';
  const lines = diff.split('\n');
  const unified = /^(diff |index |--- |\+\+\+ |@@)/m.test(diff);

  return <section className={styles.file} aria-label={`待确认的文件修改 ${change.path}`}>
    <div className={styles.fileHeader}>
      <FileCode size={16} aria-hidden="true" />
      <span className={styles.filePath} title={change.path}>{change.path}</span>
      <span className={styles.pendingLabel}>待确认</span>
    </div>
    {pairedText && textDiff ? <div className={styles.diff} role="region" aria-label={`${change.path} 修改差异`}>
      {textDiff.map((line, index) => <DiffLineRow key={index} line={line} />)}
    </div> : pairedText ? <div className={styles.diff} role="region" aria-label={`${change.path} 修改前后内容`}>
      <div className={styles.previewLabel}>内容较长，分别展示修改前与拟写内容</div>
      <div className={styles.previewLabel}>修改前</div>
      <pre className={styles.previewText}>{change.old_text}</pre>
      <div className={styles.previewLabel}>拟写内容</div>
      <pre className={styles.previewText}>{change.new_text}</pre>
    </div> : diff && unified ? <div className={styles.diff} role="region" aria-label={`${change.path} 修改差异`}>
      {lines.map((line, index) => /^(diff |index |--- |\+\+\+ |@@)/.test(line)
        ? <div key={index} className={styles.diffHeader}>{line}</div>
        : <DiffLineRow key={index} line={{ oldNum: null, newNum: null,
          type: line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : 'context',
          text: /^[+ -]/.test(line) ? line.slice(1) : line }} />)}
    </div> : diff ? <div className={styles.diff} role="region" aria-label={`${change.path} 新内容预览`}>
      <div className={styles.previewLabel}>引擎提供的新内容</div>
      <pre className={styles.previewText}>{diff}</pre>
    </div> : <p className={styles.noDiff}>引擎未提供修改差异，请核对文件路径。</p>}
  </section>;
}

export default function RuntimeApprovalCard({ approval, busy, onDecision }: {
  approval: RuntimeApproval;
  busy: boolean;
  onDecision: (decision: 'accept' | 'decline') => void;
}) {
  const changes = (approval.changes || []) as ApprovalChange[];
  const isPlan = approval.kind === 'plan/requestApproval';
  const isFile = approval.kind.includes('fileChange') || changes.length > 0;
  const canAccept = approval.allowed.includes('accept');
  const canDecline = approval.allowed.includes('decline');

  return <section className={styles.card} aria-label={isPlan ? '引擎计划审批' : '引擎操作审批'}>
    <div className={styles.header}>
      <span className={styles.icon}><ShieldCheck size={20} weight="duotone" aria-hidden="true" /></span>
      <div className={styles.heading}>
        <h3>{isPlan ? '引擎提出计划' : '引擎请求你的确认'}</h3>
        <p>{isPlan ? '请核对计划内容并决定是否接受' : isFile ? '请检查拟修改的文件和内容' : '请检查即将执行的操作'}</p>
      </div>
      <span className={styles.pendingLabel}>等待审批</span>
    </div>

    <div className={styles.body}>
      {isPlan && <div className={styles.field}>
        <div className={styles.label}>计划名称</div>
        <p className={styles.reason}>{approval.title || approval.command?.replace(/^计划：/, '') || '未命名计划'}</p>
        <div className={styles.label}>计划内容</div>
        <pre className={styles.command}>{approval.plan || approval.reason || '引擎未提供计划详情'}</pre>
      </div>}
      {!isPlan && approval.reason && <div className={styles.field}>
        <div className={styles.label}>请求说明</div>
        <p className={styles.reason}>{approval.reason}</p>
      </div>}
      {!isPlan && approval.command && <div className={styles.field}>
        <div className={styles.label}>执行内容</div>
        <pre className={styles.command}>{approval.command}</pre>
      </div>}
      {changes.length > 0 && <div className={styles.field}>
        <div className={styles.label}>文件修改（{changes.length}）</div>
        <div className={styles.files}>{changes.map((change, index) => <FilePreview key={`${change.path}-${index}`} change={change} />)}</div>
      </div>}
      {!isPlan && !approval.reason && !approval.command && !changes.length &&
        <p className={styles.noDetails}>引擎未提供可显示的操作详情。</p>}
    </div>

    {(canAccept || canDecline) && <div className={styles.actions}>
      {canDecline && <Popconfirm title={isPlan ? '拒绝这份计划？' : '拒绝本次操作？'} onConfirm={() => onDecision('decline')}
        okText="拒绝" cancelText="返回" okButtonProps={{ danger: true }} disabled={busy}>
        <Button danger size="small" icon={<X size={14} />} disabled={busy}>{isPlan ? '拒绝计划' : '拒绝本次'}</Button>
      </Popconfirm>}
      {canAccept && <Button type="primary" size="small" icon={<Check size={14} />} disabled={busy}
        onClick={() => onDecision('accept')}>{isPlan ? '接受计划' : '允许本次'}</Button>}
    </div>}
  </section>;
}

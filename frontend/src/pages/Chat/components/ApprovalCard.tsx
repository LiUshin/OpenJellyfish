import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { Button, Tag, Input, Checkbox, Space, Popconfirm } from 'antd';
import {
  Check,
  X,
  PencilSimple,
  FileCode,
  FileArrowUp,
  ListChecks,
  Plus,
  Trash,
  ShieldCheck,
  CaretDown,
  CaretRight,
  ChatCircleDots,
} from '@phosphor-icons/react';
import styles from '../chat.module.css';

const { TextArea } = Input;

interface ActionArg {
  path?: string;
  file_path?: string;
  content?: string;
  old_string?: string;
  new_string?: string;
  steps?: string[];
  questions?: string[];
}

interface ActionItem {
  name: string;
  args: ActionArg;
}

interface ConfigItem {
  action_name: string;
  allowed_decisions: string[];
}

interface Decision {
  type: string;
  edited_action?: ActionItem;
}

interface ApprovalCardProps {
  actions: ActionItem[];
  configs: ConfigItem[];
  conversationId: string;
  onResume: (decisions: Decision[]) => void;
}

function computeLineDiff(
  oldLines: string[],
  newLines: string[],
): { left: Array<{ type: 'eq' | 'del'; text: string }>; right: Array<{ type: 'eq' | 'add'; text: string }> } {
  const left: Array<{ type: 'eq' | 'del'; text: string }> = [];
  const right: Array<{ type: 'eq' | 'add'; text: string }> = [];
  let oi = 0;
  let ni = 0;
  const maxLen = Math.max(oldLines.length, newLines.length);

  while (oi < oldLines.length || ni < newLines.length) {
    if (oi < oldLines.length && ni < newLines.length) {
      if (oldLines[oi] === newLines[ni]) {
        left.push({ type: 'eq', text: oldLines[oi] });
        right.push({ type: 'eq', text: newLines[ni] });
        oi++; ni++;
      } else {
        let found = false;
        const lookAhead = Math.min(5, maxLen);
        for (let d = 1; d <= lookAhead; d++) {
          if (ni + d < newLines.length && oldLines[oi] === newLines[ni + d]) {
            for (let j = 0; j < d; j++) { left.push({ type: 'eq', text: '' }); right.push({ type: 'add', text: newLines[ni + j] }); }
            left.push({ type: 'eq', text: oldLines[oi] }); right.push({ type: 'eq', text: newLines[ni + d] });
            ni += d + 1; oi++; found = true; break;
          }
          if (oi + d < oldLines.length && oldLines[oi + d] === newLines[ni]) {
            for (let j = 0; j < d; j++) { left.push({ type: 'del', text: oldLines[oi + j] }); right.push({ type: 'eq', text: '' }); }
            left.push({ type: 'eq', text: oldLines[oi + d] }); right.push({ type: 'eq', text: newLines[ni] });
            oi += d + 1; ni++; found = true; break;
          }
        }
        if (!found) {
          left.push({ type: 'del', text: oldLines[oi] });
          right.push({ type: 'add', text: newLines[ni] });
          oi++; ni++;
        }
      }
    } else if (oi < oldLines.length) {
      left.push({ type: 'del', text: oldLines[oi] }); right.push({ type: 'eq', text: '' }); oi++;
    } else {
      left.push({ type: 'eq', text: '' }); right.push({ type: 'add', text: newLines[ni] }); ni++;
    }
  }
  return { left, right };
}

function FileActionCard({
  action, config, decision, onDecide,
}: {
  action: ActionItem;
  config: ConfigItem | undefined;
  decision: Decision | undefined;
  onDecide: (d: Decision) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editedContent, setEditedContent] = useState(
    action.name === 'write_file' ? action.args.content ?? '' : action.args.new_string ?? '',
  );

  const isWrite = action.name === 'write_file';
  const allowed = config?.allowed_decisions ?? ['approve', 'reject', 'edit'];

  const diff = useMemo(() => {
    if (isWrite) return null;
    return computeLineDiff(
      (action.args.old_string ?? '').split('\n'),
      (action.args.new_string ?? '').split('\n'),
    );
  }, [action, isWrite]);

  const addCount = isWrite
    ? (action.args.content ?? '').split('\n').length
    : diff?.right.filter(l => l.type === 'add').length ?? 0;
  const delCount = isWrite ? 0 : diff?.left.filter(l => l.type === 'del').length ?? 0;

  const handleApprove = (e: React.MouseEvent) => { e.stopPropagation(); onDecide({ type: 'approve' }); };
  const handleReject = () => onDecide({ type: 'reject' });
  const handleSaveEdit = () => {
    setEditing(false);
    onDecide({
      type: 'edit',
      edited_action: {
        name: action.name,
        args: isWrite ? { ...action.args, content: editedContent } : { ...action.args, new_string: editedContent },
      },
    });
  };

  const badge = decision && (
    <Tag
      color={decision.type === 'approve' ? 'success' : decision.type === 'reject' ? 'error' : 'warning'}
    >
      {decision.type === 'approve' ? '已批准' : decision.type === 'reject' ? '已拒绝' : '已编辑'}
    </Tag>
  );

  if (!expanded) {
    return (
      <div className={styles.fileBar} onClick={() => !decision && setExpanded(true)}>
        <div className={styles.fileBarIcon}>
          {isWrite ? <FileArrowUp size={18} weight="duotone" /> : <FileCode size={18} weight="duotone" />}
        </div>
        <div className={styles.fileBarInfo}>
          <div className={styles.fileBarPath}>{action.args.path ?? action.args.file_path ?? '未知路径'}</div>
          <div className={styles.fileBarMeta}>
            {isWrite ? '新建文件' : '修改文件'}
            {addCount > 0 && <span style={{ color: 'var(--jf-success)', marginLeft: 8 }}>+{addCount}</span>}
            {delCount > 0 && <span style={{ color: 'var(--jf-error)', marginLeft: 6 }}>-{delCount}</span>}
          </div>
        </div>
        {badge}
        <div className={styles.fileBarBtns}>
          {!decision && (
            <>
              <Button size="small" type="primary" icon={<Check size={14} weight="bold" />} onClick={handleApprove}>批准</Button>
              <Button size="small" type="text" icon={<CaretDown size={14} />} onClick={(e) => { e.stopPropagation(); setExpanded(true); }} style={{ color: 'var(--jf-text-muted)' }} />
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={styles.diffPanel}>
      <div className={styles.diffPanelHeader}>
        <div className={styles.diffPanelTitle}>
          <ShieldCheck size={16} weight="duotone" color="var(--jf-warning)" />
          <span style={{ fontFamily: 'var(--jf-font-code)', fontSize: 12 }}>{action.args.path ?? action.args.file_path ?? '未知路径'}</span>
          <Tag color={isWrite ? 'blue' : 'orange'} style={{ fontSize: 10 }}>{isWrite ? '新建' : '修改'}</Tag>
          {badge}
        </div>
        <Button size="small" type="text" icon={<CaretRight size={14} />} onClick={() => setExpanded(false)} style={{ color: 'var(--jf-text-muted)' }}>
          收起
        </Button>
      </div>

      {!editing && (
        isWrite ? (
          <div className={styles.diffInline}>
            {(action.args.content ?? '').split('\n').map((line, i) => (
              <div key={i} className={`${styles.diffSbsLine} ${styles.diffAdd}`}>
                <span className={styles.diffLineNum}>{i + 1}</span>{line}
              </div>
            ))}
          </div>
        ) : (
          <div className={styles.diffSbs}>
            <div className={`${styles.diffSbsPane} ${styles.diffSbsPaneLeft}`}>
              {diff?.left.map((l, i) => (
                <div key={i} className={`${styles.diffSbsLine} ${l.type === 'del' ? styles.diffDel : styles.diffEq}`}>
                  <span className={styles.diffLineNum}>{l.text !== '' ? i + 1 : ''}</span>{l.text}
                </div>
              ))}
            </div>
            <div className={styles.diffSbsPane}>
              {diff?.right.map((l, i) => (
                <div key={i} className={`${styles.diffSbsLine} ${l.type === 'add' ? styles.diffAdd : styles.diffEq}`}>
                  <span className={styles.diffLineNum}>{l.text !== '' ? i + 1 : ''}</span>{l.text}
                </div>
              ))}
            </div>
          </div>
        )
      )}

      {editing && (
        <TextArea
          value={editedContent}
          onChange={(e) => setEditedContent(e.target.value)}
          autoSize={{ minRows: 3, maxRows: 20 }}
          className={styles.approvalEditor}
          style={{ margin: '12px 16px' }}
        />
      )}

      {!decision && (
        <div className={styles.diffPanelActions}>
          {allowed.includes('edit') && !editing && (
            <Button size="small" icon={<PencilSimple size={14} />} onClick={() => setEditing(true)}>编辑</Button>
          )}
          {editing && (
            <Button size="small" type="primary" onClick={handleSaveEdit}>保存编辑</Button>
          )}
          {allowed.includes('reject') && (
            <Popconfirm title="确认拒绝此操作？" onConfirm={handleReject} okText="确认" cancelText="取消">
              <Button size="small" danger icon={<X size={14} weight="bold" />}>拒绝</Button>
            </Popconfirm>
          )}
          {allowed.includes('approve') && (
            <Button size="small" type="primary" icon={<Check size={14} weight="bold" />} onClick={handleApprove}>批准</Button>
          )}
        </div>
      )}
    </div>
  );
}

function PlanActionCard({
  action, config, decision, onDecide,
}: {
  action: ActionItem;
  config: ConfigItem | undefined;
  decision: Decision | undefined;
  onDecide: (d: Decision) => void;
}) {
  const [steps, setSteps] = useState<string[]>([...(action.args.steps ?? [])]);
  const [edited, setEdited] = useState(false);
  const allowed = config?.allowed_decisions ?? ['approve', 'reject'];

  const updateStep = (idx: number, val: string) => {
    setSteps(s => { const n = [...s]; n[idx] = val; return n; });
    setEdited(true);
  };
  const removeStep = (idx: number) => {
    setSteps(s => s.filter((_, i) => i !== idx));
    setEdited(true);
  };
  const addStep = () => { setSteps(s => [...s, '']); setEdited(true); };

  const handleApprove = () => {
    if (edited) {
      onDecide({ type: 'edit', edited_action: { name: action.name, args: { ...action.args, steps } } });
    } else {
      onDecide({ type: 'approve' });
    }
  };
  const handleReject = () => onDecide({ type: 'reject' });

  const badge = decision && (
    <Tag color={decision.type === 'approve' ? 'success' : decision.type === 'reject' ? 'error' : 'warning'}>
      {decision.type === 'approve' ? '已批准' : decision.type === 'reject' ? '已拒绝' : '已编辑'}
    </Tag>
  );

  return (
    <>
      <div style={{ padding: '14px 16px 0', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 'var(--jf-radius-md)',
          background: 'rgba(var(--jf-secondary-rgb), 0.12)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--jf-secondary)',
        }}>
          <ListChecks size={22} weight="duotone" />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>执行计划确认</div>
          <div style={{ fontSize: 11, color: 'var(--jf-text-muted)' }}>Agent 准备执行 {steps.length} 个步骤</div>
        </div>
        {badge}
      </div>

      <div className={styles.planTimeline}>
        {steps.map((step, i) => (
          <div key={i} className={styles.planNode}>
            <div className={styles.planDot}>{i + 1}</div>
            <div className={styles.planNodeContent}>
              <Input
                value={step}
                onChange={(e) => updateStep(i, e.target.value)}
                disabled={!!decision}
                size="small"
                className={styles.planStepInput}
                placeholder={`步骤 ${i + 1}`}
              />
              {!decision && (
                <Button type="text" size="small" danger icon={<Trash size={14} />} onClick={() => removeStep(i)} />
              )}
            </div>
          </div>
        ))}
      </div>

      {action.args.questions && action.args.questions.length > 0 && (
        <div className={styles.planQuestions}>
          <div style={{ fontSize: 12, color: 'var(--jf-warning)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
            <ChatCircleDots size={14} /> Agent 的问题
          </div>
          {action.args.questions.map((q, i) => (
            <div key={i} className={styles.planQuestion}>{q}</div>
          ))}
        </div>
      )}

      {!decision && (
        <div className={styles.planFooter}>
          <Button size="small" icon={<Plus size={14} weight="bold" />} onClick={addStep} type="dashed">
            插入步骤
          </Button>
          <Space>
            {allowed.includes('reject') && (
              <Popconfirm title="确认拒绝此计划？" onConfirm={handleReject} okText="确认" cancelText="取消">
                <Button size="small" danger icon={<X size={14} weight="bold" />}>中止</Button>
              </Popconfirm>
            )}
            {allowed.includes('approve') && (
              <Button size="small" type="primary" icon={<Check size={14} weight="bold" />} onClick={handleApprove}>
                {edited ? '提交修改' : '确认执行'}
              </Button>
            )}
          </Space>
        </div>
      )}
    </>
  );
}

export default function ApprovalCard({ actions, configs, onResume }: ApprovalCardProps) {
  const [decisions, setDecisions] = useState<(Decision | undefined)[]>(
    () => new Array(actions.length).fill(undefined),
  );
  const [batchSelected, setBatchSelected] = useState<boolean[]>(
    () => new Array(actions.length).fill(true),
  );
  const submittedRef = useRef(false);
  const onResumeRef = useRef(onResume);
  onResumeRef.current = onResume;

  const fileActions = actions.filter(a => a.name !== 'propose_plan');
  const isBatch = fileActions.length > 1;
  const allDecided = decisions.every(d => d !== undefined);

  const configFor = useCallback(
    (actionName: string) => configs.find(c => c.action_name === actionName),
    [configs],
  );

  const setDecision = (idx: number, d: Decision) => {
    setDecisions(prev => { const n = [...prev]; n[idx] = d; return n; });
  };

  useEffect(() => {
    if (allDecided && !submittedRef.current) {
      submittedRef.current = true;
      onResumeRef.current(decisions.map(d => d ?? { type: 'approve' }));
    }
  }, [allDecided, decisions]);

  const batchApprove = () => {
    setDecisions(prev => prev.map((d, i) => (batchSelected[i] && !d ? { type: 'approve' } : d)));
  };
  const batchReject = () => {
    setDecisions(prev => prev.map((d, i) => (batchSelected[i] && !d ? { type: 'reject' } : d)));
  };

  return (
    <div className={styles.approvalCard}>
      {actions.map((action, idx) => {
        if (action.name === 'propose_plan') {
          return (
            <PlanActionCard
              key={idx}
              action={action}
              config={configFor(action.name)}
              decision={decisions[idx]}
              onDecide={d => setDecision(idx, d)}
            />
          );
        }

        const card = (
          <FileActionCard
            key={idx}
            action={action}
            config={configFor(action.name)}
            decision={decisions[idx]}
            onDecide={d => setDecision(idx, d)}
          />
        );

        if (isBatch) {
          return (
            <div key={idx} className={styles.approvalBatchRow}>
              <Checkbox
                checked={batchSelected[idx]}
                onChange={e => setBatchSelected(prev => { const n = [...prev]; n[idx] = e.target.checked; return n; })}
                disabled={!!decisions[idx]}
                style={{ marginLeft: 10, marginTop: 14 }}
              />
              <div style={{ flex: 1 }}>{card}</div>
            </div>
          );
        }

        return card;
      })}

      {isBatch && !allDecided && (
        <div className={styles.approvalBatchBar}>
          <Space>
            <Button size="small" type="primary" icon={<Check size={14} weight="bold" />} onClick={batchApprove}>
              批量批准
            </Button>
            <Button size="small" danger icon={<X size={14} weight="bold" />} onClick={batchReject}>
              批量拒绝
            </Button>
          </Space>
        </div>
      )}
    </div>
  );
}

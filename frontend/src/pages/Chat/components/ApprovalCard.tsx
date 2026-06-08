import { useState, useCallback, useEffect, useRef } from 'react';
import { Button, Tag, Input, Checkbox, Space, Popconfirm } from 'antd';
import {
  Check,
  X,
  PencilSimple,
  ListChecks,
  Plus,
  Trash,
  ShieldCheck,
  ChatCircleDots,
} from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import styles from '../chat.module.css';
import { FilePreviewBody } from './StreamingFilePreview';
import EditDiffViewer from './EditDiffViewer';

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

function FileActionCard({
  action, config, decision, onDecide,
}: {
  action: ActionItem;
  config: ConfigItem | undefined;
  decision: Decision | undefined;
  onDecide: (d: Decision) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [editedContent, setEditedContent] = useState(
    action.name === 'write_file' ? action.args.content ?? '' : action.args.new_string ?? '',
  );

  const isWrite = action.name === 'write_file';
  const allowed = config?.allowed_decisions ?? ['approve', 'reject', 'edit'];

  const addCount = isWrite ? (action.args.content ?? '').split('\n').length : 0;

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
      {decision.type === 'approve' ? t('approval.approved') : decision.type === 'reject' ? t('approval.rejected') : t('approval.edited')}
    </Tag>
  );

  // write_file 使用 FilePreviewBody（语法高亮 + 完整内容）；
  // edit_file 使用 EditDiffViewer（git 风格带上下文 hunk + 展开全文）。
  const filePath = action.args.path ?? action.args.file_path ?? null;

  if (isWrite) {
    return (
      <div className={styles.approvalFileBlock}>
        {!editing ? (
          <FilePreviewBody
            filePath={filePath}
            text={action.args.content ?? ''}
            kind="write"
            status={decision ? 'done' : 'pending'}
          />
        ) : (
          <div className={styles.diffPanel}>
            <div className={styles.diffPanelHeader}>
              <div className={styles.diffPanelTitle}>
                <ShieldCheck size={16} weight="duotone" color="var(--jf-warning)" />
                <span style={{ fontFamily: 'var(--jf-font-code)', fontSize: 12 }}>{filePath ?? t('approval.unknownPath')}</span>
                <Tag color="blue" style={{ fontSize: 10 }}>{t('approval.editingTag')}</Tag>
              </div>
            </div>
            <TextArea
              value={editedContent}
              onChange={(e) => setEditedContent(e.target.value)}
              autoSize={{ minRows: 6, maxRows: 24 }}
              className={styles.approvalEditor}
              style={{ margin: '12px 16px' }}
            />
          </div>
        )}

        {!decision && (
          <div className={styles.approvalActionBar}>
            <div className={styles.approvalActionBarMeta}>
              {!editing && addCount > 0 && (
                <span style={{ color: 'var(--jf-success)' }}>{t('approval.linesAdded', { n: addCount })}</span>
              )}
              {badge}
            </div>
            <Space size={6}>
              {allowed.includes('edit') && !editing && (
                <Button size="small" icon={<PencilSimple size={14} />} onClick={() => setEditing(true)}>{t('approval.edit')}</Button>
              )}
              {editing && (
                <Button size="small" type="primary" onClick={handleSaveEdit}>{t('approval.saveEdit')}</Button>
              )}
              {allowed.includes('reject') && (
                <Popconfirm title={t('approval.rejectConfirm')} onConfirm={handleReject} okText={t('common.confirm')} cancelText={t('common.cancel')}>
                  <Button size="small" danger icon={<X size={14} weight="bold" />}>{t('approval.reject')}</Button>
                </Popconfirm>
              )}
              {allowed.includes('approve') && (
                <Button size="small" type="primary" icon={<Check size={14} weight="bold" />} onClick={handleApprove}>{t('approval.approve')}</Button>
              )}
            </Space>
          </div>
        )}
        {decision && (
          <div className={styles.approvalActionBar}>
            <div className={styles.approvalActionBarMeta}>{badge}</div>
          </div>
        )}
      </div>
    );
  }

  // ── edit_file 分支：EditDiffViewer + 统一 actionBar ─────────────
  return (
    <div className={styles.approvalFileBlock}>
      {!editing ? (
        <EditDiffViewer
          filePath={filePath ?? t('approval.unknownPath')}
          oldString={action.args.old_string ?? ''}
          newString={action.args.new_string ?? ''}
          status={decision ? 'done' : 'pending'}
        />
      ) : (
        <div className={styles.diffPanel}>
          <div className={styles.diffPanelHeader}>
            <div className={styles.diffPanelTitle}>
              <ShieldCheck size={16} weight="duotone" color="var(--jf-warning)" />
              <span style={{ fontFamily: 'var(--jf-font-code)', fontSize: 12 }}>{filePath ?? t('approval.unknownPath')}</span>
              <Tag color="blue" style={{ fontSize: 10 }}>{t('approval.editingNewString')}</Tag>
            </div>
          </div>
          <TextArea
            value={editedContent}
            onChange={(e) => setEditedContent(e.target.value)}
            autoSize={{ minRows: 6, maxRows: 24 }}
            className={styles.approvalEditor}
            style={{ margin: '12px 16px' }}
          />
        </div>
      )}

      {!decision && (
        <div className={styles.approvalActionBar}>
          <div className={styles.approvalActionBarMeta}>{badge}</div>
          <Space size={6}>
            {allowed.includes('edit') && !editing && (
              <Button size="small" icon={<PencilSimple size={14} />} onClick={() => setEditing(true)}>{t('approval.edit')}</Button>
            )}
            {editing && (
              <Button size="small" type="primary" onClick={handleSaveEdit}>{t('approval.saveEdit')}</Button>
            )}
            {allowed.includes('reject') && (
              <Popconfirm title={t('approval.rejectConfirm')} onConfirm={handleReject} okText={t('common.confirm')} cancelText={t('common.cancel')}>
                <Button size="small" danger icon={<X size={14} weight="bold" />}>{t('approval.reject')}</Button>
              </Popconfirm>
            )}
            {allowed.includes('approve') && (
              <Button size="small" type="primary" icon={<Check size={14} weight="bold" />} onClick={handleApprove}>{t('approval.approve')}</Button>
            )}
          </Space>
        </div>
      )}
      {decision && (
        <div className={styles.approvalActionBar}>
          <div className={styles.approvalActionBarMeta}>{badge}</div>
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
  const { t } = useTranslation();
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
      {decision.type === 'approve' ? t('approval.approved') : decision.type === 'reject' ? t('approval.rejected') : t('approval.edited')}
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
          <div style={{ fontWeight: 600, fontSize: 14 }}>{t('approval.planTitle')}</div>
          <div style={{ fontSize: 11, color: 'var(--jf-text-muted)' }}>{t('approval.planSubtitle', { count: steps.length })}</div>
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
                placeholder={t('approval.planStepPh', { n: i + 1 })}
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
            <ChatCircleDots size={14} /> {t('approval.planAgentQuestions')}
          </div>
          {action.args.questions.map((q, i) => (
            <div key={i} className={styles.planQuestion}>{q}</div>
          ))}
        </div>
      )}

      {!decision && (
        <div className={styles.planFooter}>
          <Button size="small" icon={<Plus size={14} weight="bold" />} onClick={addStep} type="dashed">
            {t('approval.planAddStep')}
          </Button>
          <Space>
            {allowed.includes('reject') && (
              <Popconfirm title={t('approval.planRejectConfirm')} onConfirm={handleReject} okText={t('common.confirm')} cancelText={t('common.cancel')}>
                <Button size="small" danger icon={<X size={14} weight="bold" />}>{t('approval.planAbort')}</Button>
              </Popconfirm>
            )}
            {allowed.includes('approve') && (
              <Button size="small" type="primary" icon={<Check size={14} weight="bold" />} onClick={handleApprove}>
                {edited ? t('approval.planSubmitEdit') : t('approval.planConfirmRun')}
              </Button>
            )}
          </Space>
        </div>
      )}
    </>
  );
}

export default function ApprovalCard({ actions, configs, onResume }: ApprovalCardProps) {
  const { t } = useTranslation();
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
              {t('approval.batchApprove')}
            </Button>
            <Button size="small" danger icon={<X size={14} weight="bold" />} onClick={batchReject}>
              {t('approval.batchReject')}
            </Button>
          </Space>
        </div>
      )}
    </div>
  );
}

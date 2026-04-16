import { useState } from 'react';
import {
  ListChecks, Check, X, CaretDown, MinusCircle, Lightning,
} from '@phosphor-icons/react';
import type { PlanStep } from '../../../stores/streamContext';
import styles from '../chat.module.css';

interface PlanTrackerProps {
  steps: PlanStep[];
  defaultCollapsed?: boolean;
}

const DONE_STATUSES = new Set(['done', 'completed']);

function StepIcon({ status, index }: { status: string; index: number }) {
  if (DONE_STATUSES.has(status)) return <Check size={12} weight="bold" />;
  if (status === 'failed') return <X size={12} weight="bold" />;
  if (status === 'skipped') return <MinusCircle size={10} weight="bold" />;
  return <span>{index + 1}</span>;
}

export default function PlanTracker({ steps, defaultCollapsed = false }: PlanTrackerProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  if (steps.length === 0) return null;

  const doneCount = steps.filter(s => DONE_STATUSES.has(s.status)).length;
  const total = steps.length;
  const pct = Math.round((doneCount / total) * 100);
  const allDone = doneCount === total;

  return (
    <div className={styles.planTracker}>
      <div className={styles.planTrackerHeader} onClick={() => setCollapsed(c => !c)}>
        <div className={styles.planTrackerIcon}>
          <ListChecks size={18} weight="duotone" />
        </div>
        <div className={styles.planTrackerTitleArea}>
          <div className={styles.planTrackerTitle}>
            {allDone ? '计划已完成' : '执行计划'}
          </div>
          <div className={styles.planTrackerSubtitle}>
            <span className={styles.planTrackerFraction}>{doneCount}/{total}</span>
            <span>步骤完成</span>
            <div className={styles.planProgressMini}>
              <div className={styles.planProgressMiniFill} style={{ width: `${pct}%` }} />
            </div>
          </div>
        </div>
        <div className={`${styles.planCollapseBtn} ${collapsed ? styles.collapsed : ''}`}>
          <CaretDown size={14} />
        </div>
      </div>

      {!collapsed && (
        <div className={styles.planSteps}>
          {steps.map((step, i) => {
            const cls = DONE_STATUSES.has(step.status) ? 'done' : step.status;
            return (
              <div
                key={i}
                className={`${styles.planStep} ${styles[cls] || ''}`}
              >
                <div className={styles.planStepIndicator}>
                  <StepIcon status={step.status} index={i} />
                </div>
                <div className={styles.planStepContent}>
                  <div className={styles.planStepText}>{step.content}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function PlanCompactBar({
  steps,
  onClick,
}: {
  steps: PlanStep[];
  onClick?: () => void;
}) {
  if (steps.length === 0) return null;

  const doneCount = steps.filter(s => DONE_STATUSES.has(s.status)).length;
  const total = steps.length;
  const pct = Math.round((doneCount / total) * 100);
  const current = steps.find(s => s.status === 'in_progress');
  const allDone = doneCount === total;

  return (
    <div className={styles.planCompactBar} onClick={onClick}>
      <div className={styles.planCompactIcon}>
        <Lightning size={15} weight="fill" />
      </div>
      <div className={styles.planCompactInfo}>
        <span className={styles.planCompactLabel}>
          {allDone ? '计划已完成' : '执行计划'}
        </span>
        {current && (
          <span className={styles.planCompactStep}>{current.content}</span>
        )}
      </div>
      <div className={styles.planCompactProgress}>
        <span className={styles.planCompactFraction}>{doneCount}/{total}</span>
        <div className={styles.planCompactTrack}>
          <div className={styles.planCompactFill} style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

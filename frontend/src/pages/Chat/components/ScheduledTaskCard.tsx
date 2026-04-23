/**
 * ScheduledTaskCard — 渲染 `scheduled_task` 工具块（来自后端 scheduler.py）。
 *
 * 用于在对话历史中清晰区分「定时任务自动产出」与「agent 直接回复」，
 * 避免用户/管理员把任务结果误以为是同步回复。
 *
 * 数据契约（与 app/services/scheduler.py::_build_scheduled_task_block 同步）：
 *   block.name === 'scheduled_task'
 *   block.args   = JSON.stringify({task_id, task_name, schedule_type,
 *                                   scheduled_at, scope, status, error?})
 *   block.result = 完整任务输出文本（markdown）
 *
 * 两种渲染模式：
 *   - admin（默认）：完整元数据 + 折叠/展开正文
 *   - friendlyMode：service-chat 端使用，仅显示「系统通知 · 时间」+ 正文，
 *     不暴露 task_id / scope / 内部命名，避免泄露后台细节给消费者。
 */

import { useState } from 'react';
import { Calendar, CheckCircle, WarningCircle, Bell, CaretDown } from '@phosphor-icons/react';
import type { ToolBlock } from '../types';
import { renderMarkdown } from '../markdown';
import styles from '../chat.module.css';

interface TaskMeta {
  task_id?: string;
  task_name?: string;
  schedule_type?: 'cron' | 'once' | 'interval';
  scheduled_at?: string;
  scope?: 'admin' | 'service';
  service_id?: string;
  status?: 'success' | 'error';
  error?: string;
}

function parseMeta(args: string): TaskMeta {
  if (!args) return {};
  try {
    const obj = JSON.parse(args);
    return typeof obj === 'object' && obj !== null ? obj : {};
  } catch {
    return {};
  }
}

function formatTime(iso?: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    if (sameDay) return `今天 ${hh}:${mm}`;
    const mo = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${mo}-${day} ${hh}:${mm}`;
  } catch {
    return iso;
  }
}

function scheduleLabel(t?: string): string {
  switch (t) {
    case 'cron': return '周期任务';
    case 'once': return '一次性';
    case 'interval': return '间隔任务';
    default: return '定时任务';
  }
}

interface Props {
  block: ToolBlock;
  /** 服务端（消费者）渲染：隐藏内部元数据，只显示「系统通知」标签 + 正文。 */
  friendlyMode?: boolean;
}

export default function ScheduledTaskCard({ block, friendlyMode = false }: Props) {
  // 默认展开：定时任务结果用户多半想直接看到
  const [expanded, setExpanded] = useState(true);
  const meta = parseMeta(block.args);
  const isError = meta.status === 'error';
  const result = block.result || '';

  const headerLabel = friendlyMode
    ? '系统通知'
    : (meta.task_name || scheduleLabel(meta.schedule_type));
  const timeStr = formatTime(meta.scheduled_at);

  const StatusIcon = isError ? WarningCircle : CheckCircle;
  const HeaderIcon = friendlyMode ? Bell : Calendar;

  return (
    <div
      className={`${styles.scheduledTaskCard} ${isError ? styles.scheduledTaskCardError : ''}`}
      data-task-id={friendlyMode ? undefined : meta.task_id}
    >
      <div
        className={styles.scheduledTaskHeader}
        onClick={() => setExpanded(!expanded)}
      >
        <span className={styles.scheduledTaskIconBadge}>
          <HeaderIcon size={16} weight="fill" />
        </span>
        <div className={styles.scheduledTaskHeaderText}>
          <div className={styles.scheduledTaskTitle}>
            <span className={styles.scheduledTaskTitleText}>{headerLabel}</span>
            {!friendlyMode && meta.schedule_type && (
              <span className={styles.scheduledTaskScheduleBadge}>
                {scheduleLabel(meta.schedule_type)}
              </span>
            )}
            <span
              className={`${styles.scheduledTaskStatus} ${isError ? styles.scheduledTaskStatusError : ''}`}
              aria-label={isError ? '失败' : '成功'}
            >
              <StatusIcon size={13} weight="fill" />
            </span>
          </div>
          {timeStr && (
            <div className={styles.scheduledTaskMeta}>{timeStr}</div>
          )}
        </div>
        <span
          className={`${styles.scheduledTaskChevron} ${expanded ? styles.scheduledTaskChevronOpen : ''}`}
        >
          <CaretDown size={14} weight="bold" />
        </span>
      </div>
      {expanded && (
        <div className={styles.scheduledTaskBody}>
          {isError && meta.error && (
            <div className={styles.scheduledTaskError}>
              {meta.error}
            </div>
          )}
          <div
            className={styles.scheduledTaskContent}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(result) }}
          />
        </div>
      )}
    </div>
  );
}

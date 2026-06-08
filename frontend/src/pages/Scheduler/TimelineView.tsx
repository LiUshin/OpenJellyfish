/**
 * TimelineView — SVG horizontal timeline of past + scheduled task runs.
 *
 * Renders one swim-lane per task in the current tree (root + descendants).
 * Each lane shows:
 *   - past runs as filled bars (start_at → finished_at), colored by status
 *   - the next scheduled run as a hollow marker at next_run_at
 *
 * Time axis auto-fits the visible window (min start → max next_run_at, or
 * "now + 1h" if everything is in the past).  Pure SVG so we don't pull in
 * another viz dep.
 */
import { useEffect, useMemo, useState, useCallback } from 'react';
import { Spin, Empty, Typography, Button, Space, Segmented } from 'antd';
import { ArrowsClockwise, Clock as ClockIcon } from '@phosphor-icons/react';

import type { TaskTreeNode, TaskData, RunData } from './types';
import { getSchedulerTaskTree } from '../../services/api';
import { fmtUserTime } from '../../utils/timezone';

interface TimelineViewProps {
  rootTaskId: string;
  serviceId?: string;
  selectedTaskId?: string;
  onSelectTask: (taskId: string) => void;
  refreshNonce?: number;
}

const LANE_H = 40;
const HEADER_H = 60;
const PADDING_X = 200;        // left gutter for task names
const PADDING_RIGHT = 40;
const STATUS_FG: Record<string, string> = {
  success: 'var(--jf-success)',
  error:   'var(--jf-error)',
  timeout: 'var(--jf-warning)',
  running: 'var(--jf-accent)',
};

/** Flatten tree (DFS, root first) preserving spawn_depth for indentation. */
function flatten(root: TaskTreeNode, depth = 0): { task: TaskData; depth: number }[] {
  const out: { task: TaskData; depth: number }[] = [
    { task: root.meta, depth },
  ];
  for (const c of root.children || []) {
    out.push(...flatten(c, depth + 1));
  }
  return out;
}

type Window = '24h' | '7d' | 'all';

export default function TimelineView({
  rootTaskId, serviceId, selectedTaskId, onSelectTask, refreshNonce,
}: TimelineViewProps) {
  const [tree, setTree] = useState<TaskTreeNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [windowMode, setWindowMode] = useState<Window>('24h');

  const reload = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const t = await getSchedulerTaskTree(rootTaskId,
        { maxDepth: 10, serviceId }) as TaskTreeNode;
      setTree(t);
    } catch (e) {
      setErr((e as Error).message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, [rootTaskId, serviceId]);

  useEffect(() => { reload(); }, [reload, refreshNonce]);

  const lanes = useMemo(
    () => tree ? flatten(tree) : [],
    [tree],
  );

  /** Compute time window for the X axis based on user's selection. */
  const { tMin, tMax } = useMemo(() => {
    const now = Date.now();
    if (windowMode === '24h') {
      return { tMin: now - 24 * 3600_000, tMax: now + 24 * 3600_000 };
    }
    if (windowMode === '7d') {
      return { tMin: now - 7 * 24 * 3600_000, tMax: now + 7 * 24 * 3600_000 };
    }
    // 'all': stretch to actual data
    let lo = now, hi = now;
    for (const { task } of lanes) {
      for (const r of task.runs || []) {
        const ts = r.started_at ? Date.parse(r.started_at) : NaN;
        if (!isNaN(ts)) lo = Math.min(lo, ts);
        const te = r.finished_at ? Date.parse(r.finished_at) : ts;
        if (!isNaN(te)) hi = Math.max(hi, te);
      }
      if (task.next_run_at) {
        const tn = Date.parse(task.next_run_at);
        if (!isNaN(tn)) hi = Math.max(hi, tn);
      }
    }
    if (lo === hi) { lo -= 3600_000; hi += 3600_000; }
    return { tMin: lo, tMax: hi };
  }, [lanes, windowMode]);

  const width = 1200;
  const usableW = width - PADDING_X - PADDING_RIGHT;
  const height = HEADER_H + lanes.length * LANE_H + 20;
  const xOf = (t: number) =>
    PADDING_X + ((t - tMin) / (tMax - tMin)) * usableW;

  /** Build axis tick marks: ~6 evenly-spaced labels across the window. */
  const ticks = useMemo(() => {
    const N = 6;
    const out: { t: number; label: string }[] = [];
    for (let i = 0; i <= N; i++) {
      const t = tMin + (i / N) * (tMax - tMin);
      out.push({
        t,
        label: fmtUserTime(new Date(t).toISOString(),
                           windowMode === '24h' ? 'time' : 'datetime') || '',
      });
    }
    return out;
  }, [tMin, tMax, windowMode]);

  if (err) {
    return <Empty description={err} image={Empty.PRESENTED_IMAGE_SIMPLE}
                  style={{ padding: 60 }} />;
  }

  return (
    // See GraphView for the rationale — ``position: absolute; inset: 0``
    // sidesteps the nested-flex height-collapse issue. The parent wrapper in
    // index.tsx is ``position: relative; overflow: hidden``.
    <div style={{
      position: 'absolute', inset: 0, overflow: 'auto',
      background: 'var(--jf-bg-deep)',
    }}>
      {/* Toolbar */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 5,
        padding: '10px 16px',
        background: 'var(--jf-bg-panel)',
        borderBottom: '1px solid var(--jf-border)',
        display: 'flex', alignItems: 'center', gap: 12,
      }}>
        <ClockIcon size={16} color="var(--jf-accent)" />
        <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>
          时间轴 · {lanes.length} 个任务
        </Typography.Text>
        <Segmented
          size="small"
          value={windowMode}
          onChange={v => setWindowMode(v as Window)}
          options={[
            { label: '24小时', value: '24h' },
            { label: '7天',   value: '7d' },
            { label: '全部',  value: 'all' },
          ]}
        />
        <Space size={4} style={{ marginLeft: 'auto' }}>
          <Button size="small" type="text" icon={<ArrowsClockwise size={14} />}
                  onClick={reload} loading={loading}>刷新</Button>
        </Space>
      </div>

      <Spin spinning={loading} style={{ width: '100%' }}>
        {!lanes.length && !loading && (
          <Empty description="无任务数据" style={{ padding: 60 }} />
        )}
        {lanes.length > 0 && (
          <svg width={width} height={height}
               style={{ display: 'block', margin: '12px auto' }}>
            {/* Axis ticks */}
            {ticks.map((tk, i) => (
              <g key={i}>
                <line
                  x1={xOf(tk.t)} x2={xOf(tk.t)}
                  y1={HEADER_H - 10} y2={height - 10}
                  stroke="var(--jf-border)" strokeDasharray="2 4"
                />
                <text
                  x={xOf(tk.t)} y={HEADER_H - 16}
                  textAnchor="middle"
                  fontSize="10"
                  fill="var(--jf-text-muted)"
                >{tk.label}</text>
              </g>
            ))}

            {/* "Now" line */}
            <line
              x1={xOf(Date.now())} x2={xOf(Date.now())}
              y1={HEADER_H - 10} y2={height - 10}
              stroke="var(--jf-primary)" strokeWidth={1.5}
            />
            <text
              x={xOf(Date.now())} y={HEADER_H - 30}
              textAnchor="middle" fontSize="10"
              fill="var(--jf-primary)" fontWeight={600}
            >NOW</text>

            {/* Lanes */}
            {lanes.map(({ task, depth }, idx) => {
              const y = HEADER_H + idx * LANE_H;
              const isSel = task.id === selectedTaskId;
              return (
                <g key={task.id}
                   onClick={() => onSelectTask(task.id)}
                   style={{ cursor: 'pointer' }}>
                  {/* Lane background */}
                  <rect
                    x={0} y={y}
                    width={width} height={LANE_H}
                    fill={isSel ? 'rgba(var(--jf-primary-rgb),0.08)' : 'transparent'}
                    stroke="var(--jf-border)" strokeWidth={0.5}
                  />
                  {/* Indented label */}
                  <text
                    x={12 + depth * 14}
                    y={y + LANE_H / 2 + 4}
                    fontSize="12"
                    fill={task.enabled !== false
                      ? 'var(--jf-text)' : 'var(--jf-text-muted)'}
                    fontWeight={isSel ? 600 : 400}
                  >
                    {depth > 0 ? '↳ ' : ''}
                    {task.name.slice(0, 22)}
                    {task.name.length > 22 ? '…' : ''}
                  </text>
                  {/* Past runs as bars */}
                  {(task.runs || []).map((r: RunData, i: number) => {
                    const ts = r.started_at ? Date.parse(r.started_at) : NaN;
                    const te = r.finished_at
                      ? Date.parse(r.finished_at) : ts + 60_000;
                    if (isNaN(ts)) return null;
                    if (te < tMin || ts > tMax) return null;
                    const x1 = Math.max(xOf(ts), PADDING_X);
                    const x2 = Math.min(xOf(te), width - PADDING_RIGHT);
                    const w = Math.max(2, x2 - x1);
                    const fg = STATUS_FG[r.status] || 'var(--jf-text-muted)';
                    return (
                      <rect
                        key={i}
                        x={x1} y={y + 10}
                        width={w} height={LANE_H - 20}
                        fill={fg}
                        opacity={0.7}
                        rx={3}
                      >
                        <title>
                          {`${r.status} · ${fmtUserTime(r.started_at, 'datetime')}`}
                          {r.output ? `\n${r.output.slice(0, 200)}` : ''}
                        </title>
                      </rect>
                    );
                  })}
                  {/* Next scheduled run marker */}
                  {task.next_run_at && (() => {
                    const tn = Date.parse(task.next_run_at);
                    if (isNaN(tn) || tn < tMin || tn > tMax) return null;
                    return (
                      <g key="next">
                        <circle
                          cx={xOf(tn)} cy={y + LANE_H / 2}
                          r={6}
                          fill="none"
                          stroke="var(--jf-accent)"
                          strokeWidth={2}
                        >
                          <title>
                            {`下次运行 ${fmtUserTime(task.next_run_at, 'datetime')}`}
                          </title>
                        </circle>
                      </g>
                    );
                  })()}
                </g>
              );
            })}
          </svg>
        )}
      </Spin>
    </div>
  );
}

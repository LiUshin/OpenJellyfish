import { useCallback, useEffect, useState } from 'react';
import { Drawer, Empty, Tag, Button, Tooltip, Popconfirm, message } from 'antd';
import { ArrowsClockwise, LockKey, LockKeyOpen, Robot, Clock, User } from '@phosphor-icons/react';
import * as api from '../../../services/api';
import type { WorkspaceProcess } from '../../../types';

interface Props {
  open: boolean;
  onClose: () => void;
}

const KIND_META: Record<string, { color: string; label: string; Icon: typeof Robot }> = {
  interactive: { color: 'var(--jf-primary)', label: '对话', Icon: User },
  scheduled: { color: 'var(--jf-info)', label: '定时任务', Icon: Clock },
  manual: { color: 'var(--jf-secondary)', label: '手动', Icon: Robot },
};

function fmtElapsed(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}秒`;
  if (sec < 3600) return `${Math.floor(sec / 60)}分${Math.round(sec % 60)}秒`;
  return `${Math.floor(sec / 3600)}时${Math.floor((sec % 3600) / 60)}分`;
}

/**
 * WorkspaceLockPanel — live view of every active admin process and the workspace
 * regions it holds a write lock on, with a force-release action for stuck locks.
 * Auto-refreshes every 4s while open.
 */
export default function WorkspaceLockPanel({ open, onClose }: Props) {
  const [procs, setProcs] = useState<WorkspaceProcess[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listWorkspaceLocks();
      setProcs(res.processes || []);
    } catch {
      /* keep last snapshot on transient failure */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [open, load]);

  const release = async (owner: string) => {
    try {
      await api.releaseWorkspaceLock(owner);
      message.success('已释放该进程的写锁');
      load();
    } catch {
      message.error('释放失败');
    }
  };

  return (
    <Drawer
      title="活跃进程 / 工作区锁"
      placement="right"
      width={Math.min(460, window.innerWidth)}
      open={open}
      onClose={onClose}
      extra={
        <Tooltip title="刷新">
          <Button
            type="text"
            icon={<ArrowsClockwise size={16} />}
            loading={loading}
            onClick={load}
          />
        </Tooltip>
      }
    >
      {procs.length === 0 ? (
        <Empty description="当前没有活跃进程" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {procs.map((p) => {
            const meta = KIND_META[p.kind] || KIND_META.manual;
            const hasLock = p.locked_paths.length > 0;
            return (
              <div
                key={p.owner}
                style={{
                  border: '1px solid var(--jf-border)',
                  borderLeft: `3px solid ${meta.color}`,
                  borderRadius: 'var(--jf-radius-md)',
                  padding: '12px 14px',
                  background: 'var(--jf-bg-raised)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <meta.Icon size={18} color={meta.color} weight="fill" />
                  <span style={{ fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {p.label}
                  </span>
                  <Tag color="default" style={{ margin: 0 }}>{meta.label}</Tag>
                </div>
                <div style={{ fontSize: 12, color: 'var(--jf-text-dim)', marginBottom: 8 }}>
                  已运行 {fmtElapsed(p.elapsed_sec)}
                  {p.expires_at ? ' · 有 TTL 兜底' : ''}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                  {hasLock ? (
                    <>
                      <LockKey size={14} color={meta.color} />
                      {p.locked_paths.map((path) => (
                        <Tag key={path} color={meta.color} style={{ margin: 0, fontFamily: 'JetBrains Mono, monospace' }}>
                          {path === '/' ? '/ (全部)' : path}
                        </Tag>
                      ))}
                    </>
                  ) : (
                    <span style={{ fontSize: 12, color: 'var(--jf-text-dim)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <LockKeyOpen size={14} /> 未锁定（只读）
                    </span>
                  )}
                </div>
                {hasLock && (
                  <div style={{ marginTop: 10, textAlign: 'right' }}>
                    <Popconfirm
                      title="强制释放该进程的写锁？"
                      description="仅释放锁，不会终止正在运行的 agent。"
                      okText="释放"
                      cancelText="取消"
                      onConfirm={() => release(p.owner)}
                    >
                      <Button size="small" danger type="text" icon={<LockKeyOpen size={14} />}>
                        强制释放
                      </Button>
                    </Popconfirm>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Drawer>
  );
}

/**
 * GraphView — reactflow-based pedigree visualisation of a scheduled task tree.
 *
 * Given a root task ID, fetches the full subtree from the backend
 * (`/scheduler/{root}/tree`) and lays it out top-down with dagre.  Nodes show
 * status/name/depth; clicking a node calls back to the parent so the detail
 * pane can switch to that task.
 *
 * Why dagre: directed-acyclic layered layout fits a parent→children tree
 * cleanly, and dagre is a tiny dependency that pairs naturally with reactflow.
 */
import { useEffect, useMemo, useState, useCallback } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap,
  Node, Edge, NodeProps, Handle, Position,
} from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from 'dagre';
import { Spin, Empty, Typography, Button, Space } from 'antd';
import { ArrowsClockwise, Tree as TreeIcon } from '@phosphor-icons/react';

import type { TaskTreeNode, TaskData } from './types';
import { getSchedulerTaskTree } from '../../services/api';

interface GraphViewProps {
  /** Any task ID inside the chain — we walk up to root via spawn_chain. */
  rootTaskId: string;
  serviceId?: string;
  /** Currently-selected task (highlighted). */
  selectedTaskId?: string;
  onSelectTask: (taskId: string) => void;
  /** External version bumper to trigger refresh (e.g. after run-now). */
  refreshNonce?: number;
}

const NODE_W = 220;
const NODE_H = 86;
const RANK_SEP = 80;
const NODE_SEP = 40;

/** Single task node — Ant-styled card with status + name + meta. */
function TaskNode({ data }: NodeProps<TaskNodeData>) {
  const t = data.task;
  const enabled = t.enabled !== false;
  const lastRun = (t.runs || [])[t.runs?.length ? t.runs.length - 1 : 0];
  const status = lastRun?.status || (t.next_run_at ? 'pending' : 'idle');

  // Status → border colour
  const accent: Record<string, string> = {
    success: 'var(--jf-success)',
    error: 'var(--jf-error)',
    timeout: 'var(--jf-warning)',
    running: 'var(--jf-accent)',
    pending: 'var(--jf-text-muted)',
    idle: 'var(--jf-border)',
  };
  const borderColor = data.selected
    ? 'var(--jf-primary)'
    : (accent[status] || 'var(--jf-border)');

  return (
    <div
      style={{
        width: NODE_W,
        minHeight: NODE_H,
        borderRadius: 'var(--jf-radius-lg)',
        background: enabled ? 'var(--jf-bg-panel)' : 'var(--jf-bg-deep)',
        border: `2px solid ${borderColor}`,
        boxShadow: data.selected
          ? '0 0 0 3px rgba(var(--jf-primary-rgb),0.25)'
          : '0 1px 4px rgba(0,0,0,0.15)',
        padding: '10px 12px',
        cursor: 'pointer',
        opacity: enabled ? 1 : 0.55,
        fontSize: 12,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        gap: 6, marginBottom: 4,
      }}>
        <span style={{
          fontWeight: 600, color: 'var(--jf-text)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          flex: 1,
        }}>{t.name}</span>
        <span style={{
          fontSize: 10, color: 'var(--jf-text-muted)',
          background: 'var(--jf-bg-raised)', padding: '1px 6px',
          borderRadius: 'var(--jf-radius-sm)',
        }}>d={t.spawn_depth ?? 0}</span>
      </div>
      <div style={{
        fontSize: 10, color: 'var(--jf-text-muted)',
        fontFamily: "'Cascadia Code', monospace",
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{t.id}</div>
      <div style={{
        marginTop: 4, fontSize: 10, color: 'var(--jf-text-muted)',
        display: 'flex', justifyContent: 'space-between',
      }}>
        <span>{(t.runs || []).length} 次运行</span>
        <span style={{ color: accent[status], fontWeight: 500 }}>{status}</span>
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

interface TaskNodeData {
  task: TaskData;
  selected: boolean;
}

const NODE_TYPES = { task: TaskNode };

/**
 * Walk the tree response and emit nodes + edges, then run dagre layout.
 *
 * Layout direction is top-down (TB) so the root sits at the top and children
 * fan downward.  Mirrors the spawn semantics in the backend tree.
 */
function buildLayout(root: TaskTreeNode, selectedId?: string): {
  nodes: Node<TaskNodeData>[];
  edges: Edge[];
} {
  const g = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: NODE_SEP, ranksep: RANK_SEP });

  const nodes: Node<TaskNodeData>[] = [];
  const edges: Edge[] = [];

  function visit(node: TaskTreeNode, parentId?: string) {
    const id = node.meta.id;
    g.setNode(id, { width: NODE_W, height: NODE_H });
    nodes.push({
      id,
      type: 'task',
      position: { x: 0, y: 0 },  // overwritten by dagre
      data: { task: node.meta, selected: id === selectedId },
    });
    if (parentId) {
      edges.push({
        id: `${parentId}->${id}`,
        source: parentId,
        target: id,
        type: 'smoothstep',
        style: { stroke: 'var(--jf-border-strong)', strokeWidth: 1.5 },
      });
      g.setEdge(parentId, id);
    }
    for (const c of node.children || []) visit(c, id);
  }
  visit(root);

  dagre.layout(g);
  for (const n of nodes) {
    const p = g.node(n.id);
    n.position = { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 };
  }
  return { nodes, edges };
}

export default function GraphView({
  rootTaskId, serviceId, selectedTaskId, onSelectTask, refreshNonce,
}: GraphViewProps) {
  const [tree, setTree] = useState<TaskTreeNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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

  const { nodes, edges } = useMemo(
    () => tree ? buildLayout(tree, selectedTaskId) : { nodes: [], edges: [] },
    [tree, selectedTaskId],
  );

  const handleNodeClick = useCallback((_: unknown, node: Node) => {
    onSelectTask(node.id);
  }, [onSelectTask]);

  if (err) {
    return (
      <Empty description={err} image={Empty.PRESENTED_IMAGE_SIMPLE}
             style={{ padding: 60 }} />
    );
  }

  return (
    // ``position: absolute; inset: 0`` makes us fill the parent regardless
    // of any flex-height quirks (ReactFlow needs an explicitly-sized parent,
    // and nested column-flex chains routinely collapse ``height: 100%`` to 0
    // on some browsers). The parent in index.tsx is ``position: relative``.
    <div style={{
      position: 'absolute', inset: 0,
      background: 'var(--jf-bg-deep)',
    }}>
      <div style={{
        position: 'absolute', top: 12, left: 12, zIndex: 5,
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 10px', background: 'var(--jf-bg-panel)',
        border: '1px solid var(--jf-border)',
        borderRadius: 'var(--jf-radius-md)',
      }}>
        <TreeIcon size={14} color="var(--jf-accent)" />
        <Typography.Text style={{ fontSize: 12 }}>
          谱系图 · root={rootTaskId.slice(0, 12)} · {nodes.length} 个节点
        </Typography.Text>
        <Space size={4}>
          <Button size="small" type="text" icon={<ArrowsClockwise size={14} />}
                  onClick={reload} loading={loading}>刷新</Button>
        </Space>
      </div>
      <Spin spinning={loading} style={{ width: '100%', height: '100%' }}>
        {tree && (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodeClick={handleNodeClick}
            fitView
            minZoom={0.2}
            maxZoom={1.6}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="var(--jf-border)" gap={16} />
            <Controls
              showInteractive={false}
              style={{
                background: 'var(--jf-bg-panel)',
                border: '1px solid var(--jf-border)',
              }}
            />
            <MiniMap
              pannable zoomable
              nodeColor={(n) =>
                (n.data as TaskNodeData).selected
                  ? 'var(--jf-primary)' : 'var(--jf-text-muted)'}
              style={{
                background: 'var(--jf-bg-panel)',
                border: '1px solid var(--jf-border)',
              }}
            />
          </ReactFlow>
        )}
        {!tree && !loading && (
          <Empty description="无谱系数据" style={{ padding: 60 }} />
        )}
      </Spin>
    </div>
  );
}

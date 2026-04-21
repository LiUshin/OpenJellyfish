import { useEffect, useMemo, useState, useCallback } from 'react';
import { Modal, Tree, Switch, Empty, Spin, Tag, Space, Typography } from 'antd';
import { FolderOutlined, FileOutlined } from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import * as api from '../services/api';
import type { FileItem } from '../types';

const { Text } = Typography;

interface FileTreePickerProps {
  open: boolean;
  title: string;
  /**
   * 选择器扫描的根目录（如 /docs、/scripts）。Admin 自身的文件系统视角。
   */
  rootPath: string;
  /**
   * 当前已选路径列表（相对路径，不带前导 /，文件夹以 / 结尾）；
   * 特殊值 ['*'] 表示全部
   */
  value: string[];
  /** 空选时显示给用户的提示文字（如「未选 = 不允许任何脚本」） */
  emptyHint?: string;
  /** 是否支持「全部 (*)」 快捷开关 */
  enableAllShortcut?: boolean;
  onCancel: () => void;
  onOk: (next: string[]) => void;
}

interface TreeDataNode extends DataNode {
  isLeaf: boolean;
  /**
   * 节点对应的「相对于 rootPath 的路径」：
   * - 文件：foo/bar.csv
   * - 文件夹：foo/bar/   （以斜杠结尾）
   */
  relPath: string;
  absPath: string;
}

function joinPath(...parts: string[]): string {
  const joined = parts.filter(Boolean).join('/').replace(/\/+/g, '/');
  return joined.startsWith('/') ? joined : '/' + joined;
}

function stripRoot(absPath: string, rootPath: string): string {
  const root = rootPath.replace(/\/+$/, '');
  if (!absPath.startsWith(root)) return absPath;
  return absPath.slice(root.length).replace(/^\/+/, '');
}

function toRelKey(absPath: string, rootPath: string, isDir: boolean): string {
  const rel = stripRoot(absPath, rootPath);
  if (!rel) return isDir ? './' : '';
  return isDir ? `${rel}/` : rel;
}

export default function FileTreePicker({
  open,
  title,
  rootPath,
  value,
  emptyHint,
  enableAllShortcut = true,
  onCancel,
  onOk,
}: FileTreePickerProps) {
  const [treeData, setTreeData] = useState<TreeDataNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [allMode, setAllMode] = useState(false);
  const [checkedKeys, setCheckedKeys] = useState<string[]>([]);
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
  const [missingPaths, setMissingPaths] = useState<string[]>([]);

  // 把外部 value 同步到内部 state
  useEffect(() => {
    if (!open) return;
    if (value.includes('*')) {
      setAllMode(true);
      setCheckedKeys([]);
    } else {
      setAllMode(false);
      setCheckedKeys(value || []);
    }
  }, [open, value]);

  // 把 FileItem[] 映射为 antd Tree 节点
  const buildNodes = useCallback(
    (items: FileItem[], parentAbs: string): TreeDataNode[] => {
      return items
        .slice()
        .sort((a, b) => {
          if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
          return a.name.localeCompare(b.name);
        })
        .map((item) => {
          const absPath = joinPath(parentAbs, item.name);
          const relKey = toRelKey(absPath, rootPath, item.is_dir);
          return {
            key: relKey,
            title: (
              <span style={{ fontSize: 13 }}>
                {item.name}
                {item.is_dir && <Text type="secondary" style={{ marginLeft: 4 }}>/</Text>}
              </span>
            ),
            icon: item.is_dir ? <FolderOutlined /> : <FileOutlined />,
            isLeaf: !item.is_dir,
            relPath: relKey,
            absPath,
          } as TreeDataNode;
        });
    },
    [rootPath],
  );

  // 加载根目录
  const loadRoot = useCallback(async () => {
    setLoading(true);
    try {
      const items = await api.listFiles(rootPath);
      setTreeData(buildNodes(items, rootPath));
      // 检查 value 里有没有"已选但不存在"的路径，提示给用户
      const present = new Set(items.map((it) => toRelKey(joinPath(rootPath, it.name), rootPath, it.is_dir)));
      const missing = (value || [])
        .filter((v) => v !== '*' && !v.includes('/'))  // 仅检查根级直接路径
        .filter((v) => !present.has(v));
      setMissingPaths(missing);
    } catch {
      setTreeData([]);
    } finally {
      setLoading(false);
    }
  }, [rootPath, buildNodes, value]);

  useEffect(() => {
    if (open && !allMode) {
      loadRoot();
    } else if (!open) {
      setTreeData([]);
      setExpandedKeys([]);
      setMissingPaths([]);
    }
  }, [open, allMode, loadRoot]);

  // 懒加载子目录
  const onLoadData = useCallback(
    async (node: DataNode & { absPath?: string; children?: unknown[] }): Promise<void> => {
      if (node.children && node.children.length > 0) return;
      const absPath = node.absPath;
      if (!absPath) return;
      try {
        const items = await api.listFiles(absPath);
        const children = buildNodes(items, absPath);
        setTreeData((prev) => updateTreeData(prev, node.key, children));
      } catch {
        // 静默：保持节点为空
      }
    },
    [buildNodes],
  );

  const handleOk = () => {
    if (allMode) {
      onOk(['*']);
    } else {
      onOk(checkedKeys);
    }
  };

  const switchToAllMode = (checked: boolean) => {
    setAllMode(checked);
    if (checked) {
      setCheckedKeys([]);
    }
  };

  return (
    <Modal
      title={title}
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      okText="确定"
      cancelText="取消"
      width={520}
      destroyOnClose
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Space size={8} align="center">
          <Text strong style={{ fontSize: 13 }}>根目录:</Text>
          <Tag color="purple" style={{ fontFamily: 'monospace' }}>{rootPath}</Tag>
        </Space>

        {enableAllShortcut && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '8px 12px',
              background: 'var(--jf-bg-deep)',
              borderRadius: 6,
              border: '1px solid var(--jf-border)',
            }}
          >
            <Switch checked={allMode} onChange={switchToAllMode} size="small" />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, color: 'var(--jf-text)' }}>允许全部 (<code>*</code>)</div>
              <div style={{ fontSize: 11, color: 'var(--jf-text-muted)' }}>
                打开后将忽略下方勾选；service 可访问 <code>{rootPath}</code> 下所有内容
              </div>
            </div>
          </div>
        )}

        {missingPaths.length > 0 && !allMode && (
          <div
            style={{
              fontSize: 12,
              color: '#faad14',
              padding: '6px 10px',
              background: 'rgba(250,173,20,0.08)',
              borderRadius: 4,
              border: '1px solid rgba(250,173,20,0.3)',
            }}
          >
            ⚠ 已选路径不存在：{missingPaths.join(', ')}（可在保存时移除）
          </div>
        )}

        <div
          style={{
            minHeight: 280,
            maxHeight: 420,
            overflowY: 'auto',
            border: '1px solid var(--jf-border)',
            borderRadius: 6,
            padding: '8px 4px',
            background: allMode ? 'rgba(108,92,231,0.04)' : 'transparent',
            opacity: allMode ? 0.5 : 1,
            pointerEvents: allMode ? 'none' : 'auto',
          }}
        >
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
              <Spin />
            </div>
          ) : treeData.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  目录为空。请先在文件面板上传文件到 <code>{rootPath}</code>
                </Text>
              }
              style={{ padding: '40px 0' }}
            />
          ) : (
            <Tree
              checkable
              showIcon
              loadData={onLoadData}
              treeData={treeData as DataNode[]}
              checkedKeys={checkedKeys}
              expandedKeys={expandedKeys}
              onCheck={(checked) => {
                if (Array.isArray(checked)) setCheckedKeys(checked.map(String));
                else setCheckedKeys((checked.checked || []).map(String));
              }}
              onExpand={(keys) => setExpandedKeys(keys)}
            />
          )}
        </div>

        {!allMode && (
          <div style={{ fontSize: 11, color: 'var(--jf-text-muted)', lineHeight: 1.6 }}>
            {emptyHint && checkedKeys.length === 0 && (
              <div style={{ marginBottom: 4 }}>
                <Text type="warning" style={{ fontSize: 11 }}>未选：{emptyHint}</Text>
              </div>
            )}
            勾选文件夹 = 整个目录递归允许；勾选具体文件 = 仅该文件
          </div>
        )}
      </div>
    </Modal>
  );
}

// 递归更新 treeData，把新加载的 children 挂到 key 对应的节点
function updateTreeData(
  list: TreeDataNode[],
  key: React.Key,
  children: TreeDataNode[],
): TreeDataNode[] {
  return list.map((node) => {
    if (node.key === key) {
      return { ...node, children: children as TreeDataNode[] };
    }
    if (node.children) {
      return { ...node, children: updateTreeData(node.children as TreeDataNode[], key, children) };
    }
    return node;
  });
}

// 重新导出选中值的展示工具，供外部 trigger 用
export function summarizePaths(value: string[]): string {
  if (!value || value.length === 0) return '（未设置）';
  if (value.includes('*')) return '✅ 全部';
  if (value.length <= 3) return value.join(', ');
  return `${value.slice(0, 3).join(', ')} 等 ${value.length} 项`;
}

/** 统一展示选中值的 trigger 行（按钮 + 摘要） */
export function PickerTrigger({
  value,
  onClick,
  placeholder = '点击选择…',
}: {
  value: string[];
  onClick: () => void;
  placeholder?: string;
}) {
  const isAll = value?.includes('*');
  return (
    <div
      onClick={onClick}
      style={{
        cursor: 'pointer',
        padding: '6px 11px',
        border: '1px solid var(--jf-border)',
        borderRadius: 6,
        background: 'var(--jf-bg-deep)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
        minHeight: 36,
        transition: 'border-color 0.15s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--jf-primary)')}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--jf-border)')}
    >
      <span
        style={{
          flex: 1,
          fontSize: 13,
          color: value?.length ? 'var(--jf-text)' : 'var(--jf-text-muted)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {isAll ? (
          <Tag color="green" style={{ margin: 0 }}>全部 (*)</Tag>
        ) : value && value.length > 0 ? (
          summarizePaths(value)
        ) : (
          placeholder
        )}
      </span>
      <FolderOutlined style={{ color: 'var(--jf-text-muted)' }} />
    </div>
  );
}

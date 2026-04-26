import { useEffect, useState, useCallback } from 'react';
import { Modal, Tree, Spin, Empty, Typography, Tag } from 'antd';
import { FolderOutlined, HomeOutlined } from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import * as api from '../services/api';
import type { FileItem } from '../types';

const { Text } = Typography;

interface FolderPickerProps {
  open: boolean;
  /** Modal title; defaults to 「选择目标文件夹」. */
  title?: string;
  /** Initial highlighted folder, used both as the default selection and to
   *  seed which subtree is expanded on open. */
  initialPath?: string;
  /** Paths to grey out / disable as drop targets. Used to prevent moving
   *  a folder into itself or any of its descendants. */
  disabledPaths?: string[];
  okText?: string;
  onCancel: () => void;
  onOk: (targetPath: string) => void;
}

interface FolderNode extends DataNode {
  /** Absolute path with leading slash, e.g. /docs/notes. Root is '/'. */
  absPath: string;
  isLeaf: false;
}

/** Lightweight, single-select folder picker that shows ONLY directories
 *  (files are filtered out — irrelevant for move/copy targets) and lazy-
 *  loads children on expand. Designed for the FilePanel right-click 「发送到」
 *  flow and for Ctrl+V conflict-resolution dialogs.
 *
 *  Why a new component instead of reusing FileTreePicker:
 *    FileTreePicker is multi-select (checkable) + has an "全部 (*)" mode +
 *    shows files; semantically it's a permissions picker for Service config.
 *    Forcing it into a single-folder-target role would require flipping
 *    half a dozen flags and disabling features. A small dedicated component
 *    is cheaper to maintain and lets the move/copy UX stay tight.
 */
export default function FolderPicker({
  open,
  title = '选择目标文件夹',
  initialPath = '/',
  disabledPaths = [],
  okText = '移动到这里',
  onCancel,
  onOk,
}: FolderPickerProps) {
  const [tree, setTree] = useState<FolderNode[]>([]);
  const [expanded, setExpanded] = useState<React.Key[]>(['/']);
  const [selected, setSelected] = useState<string>(initialPath || '/');
  const [loading, setLoading] = useState(false);

  const isDisabled = useCallback(
    (path: string): boolean => disabledPaths.some(
      (d) => path === d || path.startsWith(d.replace(/\/$/, '') + '/'),
    ),
    [disabledPaths],
  );

  const loadChildren = useCallback(
    async (parent: string): Promise<FolderNode[]> => {
      const items = await api.listFiles(parent);
      return items
        .filter((it: FileItem) => it.is_dir)
        .map((it: FileItem) => {
          const abs = parent === '/' ? `/${it.name}` : `${parent}/${it.name}`;
          const disabled = isDisabled(abs);
          return {
            key: abs,
            absPath: abs,
            isLeaf: false,
            disabled,
            title: (
              <span style={{ fontSize: 13, color: disabled ? 'var(--jf-text-dim)' : 'var(--jf-text)' }}>
                {it.name}
              </span>
            ),
            icon: <FolderOutlined style={{ color: disabled ? 'var(--jf-text-dim)' : 'var(--jf-primary)' }} />,
          } as FolderNode;
        });
    },
    [isDisabled],
  );

  useEffect(() => {
    if (!open) return;
    setSelected(initialPath || '/');
    setExpanded(['/']);
    setLoading(true);
    loadChildren('/')
      .then((children) => {
        setTree([
          {
            key: '/',
            absPath: '/',
            isLeaf: false,
            title: <span style={{ fontSize: 13, fontWeight: 500 }}>根目录</span>,
            icon: <HomeOutlined style={{ color: 'var(--jf-primary)' }} />,
            children,
          },
        ]);
      })
      .catch(() => setTree([]))
      .finally(() => setLoading(false));
  }, [open, initialPath, loadChildren]);

  const onLoadData = useCallback(
    async (node: DataNode): Promise<void> => {
      const n = node as unknown as FolderNode & { children?: FolderNode[] };
      if (n.children && n.children.length > 0) return;
      try {
        const children = await loadChildren(n.absPath);
        setTree((prev) => insertChildren(prev, n.absPath, children));
      } catch {
        /* silent: keep the node empty */
      }
    },
    [loadChildren],
  );

  return (
    <Modal
      title={title}
      open={open}
      onCancel={onCancel}
      onOk={() => onOk(selected)}
      okText={okText}
      cancelText="取消"
      width={460}
      okButtonProps={{ disabled: isDisabled(selected) }}
      destroyOnClose
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Text strong style={{ fontSize: 12 }}>当前选中:</Text>
          <Tag color={isDisabled(selected) ? 'red' : 'purple'} style={{ fontFamily: 'monospace', margin: 0 }}>
            {selected}
          </Tag>
          {isDisabled(selected) && (
            <Text type="danger" style={{ fontSize: 11 }}>不能选这里（源文件夹本身或它的子目录）</Text>
          )}
        </div>
        <div
          style={{
            minHeight: 280,
            maxHeight: 380,
            overflowY: 'auto',
            border: '1px solid var(--jf-border)',
            borderRadius: 6,
            padding: '8px 4px',
            background: 'var(--jf-bg-deep)',
          }}
        >
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
              <Spin />
            </div>
          ) : tree.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Text type="secondary" style={{ fontSize: 12 }}>无可选文件夹</Text>
              }
              style={{ padding: '40px 0' }}
            />
          ) : (
            <Tree
              showIcon
              loadData={onLoadData}
              treeData={tree as DataNode[]}
              expandedKeys={expanded}
              onExpand={(keys) => setExpanded(keys)}
              selectedKeys={[selected]}
              onSelect={(_, info) => {
                const node = info.node as unknown as FolderNode;
                if (!node.disabled) setSelected(node.absPath);
              }}
            />
          )}
        </div>
        <Text type="secondary" style={{ fontSize: 11 }}>
          提示：双击文件夹图标展开下一级；选中根目录 = 移动/复制到 /
        </Text>
      </div>
    </Modal>
  );
}

function insertChildren(
  list: FolderNode[],
  parentPath: string,
  children: FolderNode[],
): FolderNode[] {
  return list.map((node) => {
    if (node.absPath === parentPath) {
      return { ...node, children };
    }
    if (node.children) {
      return {
        ...node,
        children: insertChildren(node.children as FolderNode[], parentPath, children) as DataNode[],
      };
    }
    return node;
  });
}

import { useState, useEffect, useCallback, useMemo } from 'react';
import { Input, Button, Table, Space, Typography, Tag, message, Popconfirm, Spin } from 'antd';
import { Package, Plus, Trash, ArrowsClockwise, MagnifyingGlass } from '@phosphor-icons/react';
import * as api from '../../services/api';
import type { PackageInfo } from '../../services/api';

const { Text, Paragraph } = Typography;

export default function PackagesPage() {
  const [packages, setPackages] = useState<PackageInfo[]>([]);
  const [venvReady, setVenvReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [uninstalling, setUninstalling] = useState<string | null>(null);
  const [initializing, setInitializing] = useState(false);
  const [newPkg, setNewPkg] = useState('');
  const [output, setOutput] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  const filteredPackages = useMemo(() => {
    if (!searchTerm.trim()) return packages;
    const q = searchTerm.toLowerCase();
    return packages.filter(p => p.name.toLowerCase().includes(q));
  }, [packages, searchTerm]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listPackages();
      setPackages(res.packages);
      setVenvReady(res.venv_ready);
    } catch {
      message.error('加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleInit = async () => {
    setInitializing(true);
    try {
      await api.initVenv();
      message.success('环境已初始化');
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '初始化失败');
    } finally {
      setInitializing(false);
    }
  };

  const handleInstall = async () => {
    const pkg = newPkg.trim();
    if (!pkg) return;
    setInstalling(true);
    setOutput(null);
    try {
      const res = await api.installPackage(pkg);
      message.success(`已安装 ${pkg}`);
      setOutput(res.output);
      setNewPkg('');
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '安装失败');
    } finally {
      setInstalling(false);
    }
  };

  const handleUninstall = async (pkg: string) => {
    setUninstalling(pkg);
    setOutput(null);
    try {
      const res = await api.uninstallPackage(pkg);
      message.success(`已卸载 ${pkg}`);
      setOutput(res.output);
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '卸载失败');
    } finally {
      setUninstalling(null);
    }
  };

  const columns = [
    {
      title: '包名',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => <Text style={{ color: 'var(--jf-text)', fontFamily: 'monospace' }}>{name}</Text>,
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 120,
      render: (v: string) => <Tag style={{ fontFamily: 'monospace' }}>{v}</Tag>,
    },
    {
      title: '',
      key: 'action',
      width: 60,
      render: (_: unknown, record: PackageInfo) => (
        <Popconfirm title={`卸载 ${record.name}？`} onConfirm={() => handleUninstall(record.name)} okText="确定" cancelText="取消">
          <Button
            size="small" type="text" danger
            icon={<Trash size={14} />}
            loading={uninstalling === record.name}
          />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div style={{ padding: '24px 32px', maxWidth: 960, margin: '0 auto', width: '100%' }}>
      <Space align="center" size={10} style={{ marginBottom: 6 }}>
        <Package size={22} weight="duotone" color="var(--jf-accent)" />
        <Text strong style={{ fontSize: 16, color: 'var(--jf-text)' }}>Python 环境</Text>
        {venvReady ? (
          <Tag color="green" style={{ fontSize: 10 }}>已就绪</Tag>
        ) : (
          <Tag color="orange" style={{ fontSize: 10 }}>未初始化</Tag>
        )}
      </Space>
      <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 13, marginBottom: 16 }}>
        管理脚本执行的 Python 包环境。每个管理员拥有独立的虚拟环境，预装了系统级科学计算库（numpy、pandas 等），
        你可以额外安装所需的包。Service 下的 consumer 脚本也使用管理员的环境。
      </Paragraph>

      {!venvReady && (
        <div style={{
          background: 'var(--jf-bg-raised)', borderRadius: 'var(--jf-radius-lg)', padding: 24, marginBottom: 16,
          textAlign: 'center', border: '1px solid var(--jf-border)',
        }}>
          <Package size={40} weight="duotone" color="var(--jf-accent)" style={{ marginBottom: 12 }} />
          <Paragraph style={{ color: 'var(--jf-text-muted)', marginBottom: 16 }}>
            首次使用需要初始化 Python 环境。初始化会继承系统已安装的所有包，可能需要 10-30 秒。
          </Paragraph>
          <Button
            type="primary"
            icon={<Plus size={16} />}
            loading={initializing}
            onClick={handleInit}
            style={{ background: 'var(--jf-accent)', borderColor: 'var(--jf-accent)' }}
          >
            初始化环境
          </Button>
        </div>
      )}

      {venvReady && (
        <>
          <div style={{
            background: 'var(--jf-bg-raised)', borderRadius: 'var(--jf-radius-lg)', padding: '16px 20px', marginBottom: 16,
            border: '1px solid var(--jf-border)',
          }}>
            <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12, marginBottom: 8, display: 'block' }}>
              安装新的 Python 包（支持版本号，如 <code style={{ color: 'var(--jf-accent)' }}>requests==2.31.0</code>）
            </Text>
            <Space.Compact style={{ width: '100%' }}>
              <Input
                value={newPkg}
                onChange={(e) => setNewPkg(e.target.value)}
                placeholder="包名，如 beautifulsoup4 或 openai>=1.0"
                onPressEnter={handleInstall}
                style={{ background: 'var(--jf-bg-deep)', border: '1px solid var(--jf-border)', color: 'var(--jf-text)' }}
              />
              <Button
                type="primary"
                icon={<Plus size={14} />}
                loading={installing}
                onClick={handleInstall}
                disabled={!newPkg.trim()}
                style={{ background: 'var(--jf-primary)', borderColor: 'var(--jf-primary)' }}
              >
                安装
              </Button>
            </Space.Compact>
          </div>

          {output && (
            <pre style={{
              background: 'var(--jf-bg-deep)', border: '1px solid var(--jf-border)', borderRadius: 'var(--jf-radius-md)',
              padding: 12, color: 'var(--jf-text-muted)', fontSize: 11, fontFamily: 'monospace',
              maxHeight: 150, overflow: 'auto', marginBottom: 16, whiteSpace: 'pre-wrap',
            }}>
              {output}
            </pre>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 12 }}>
            <Input
              prefix={<MagnifyingGlass size={14} color="var(--jf-text-dim)" />}
              placeholder="搜索已安装的包…"
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
              allowClear
              style={{ flex: 1, maxWidth: 300, background: 'var(--jf-bg-deep)', border: '1px solid var(--jf-border)', color: 'var(--jf-text)' }}
            />
            <Space size={8}>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12, whiteSpace: 'nowrap' }}>
                {searchTerm.trim() ? `${filteredPackages.length} / ${packages.length}` : `${packages.length} 个包`}
              </Text>
              <Button
                size="small" type="text"
                icon={<ArrowsClockwise size={14} />}
                onClick={load}
                style={{ color: 'var(--jf-text-muted)' }}
              >
                刷新
              </Button>
            </Space>
          </div>

          <Spin spinning={loading}>
            <Table
              dataSource={filteredPackages}
              columns={columns}
              rowKey="name"
              size="small"
              pagination={{ pageSize: 20, showSizeChanger: false }}
              style={{ background: 'var(--jf-bg-raised)', borderRadius: 'var(--jf-radius-md)' }}
            />
          </Spin>
        </>
      )}
    </div>
  );
}

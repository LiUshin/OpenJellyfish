import { useState, useEffect, useCallback, useMemo } from 'react';
import { Input, Button, Table, Space, Typography, Tag, message, Popconfirm, Spin } from 'antd';
import { Package, Plus, Trash, ArrowsClockwise, MagnifyingGlass } from '@phosphor-icons/react';
import { useTranslation, Trans } from 'react-i18next';
import * as api from '../../services/api';
import type { PackageInfo } from '../../services/api';
import { useIsMobile } from '../../hooks/useMediaQuery';

const { Text, Paragraph } = Typography;

export default function PackagesPage() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
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
      message.error(t('packages.loadFail'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const handleInit = async () => {
    setInitializing(true);
    try {
      await api.initVenv();
      message.success(t('packages.envInited'));
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('packages.initFailed'));
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
      message.success(t('packages.installSuccess', { pkg }));
      setOutput(res.output);
      setNewPkg('');
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('packages.installFailed'));
    } finally {
      setInstalling(false);
    }
  };

  const handleUninstall = async (pkg: string) => {
    setUninstalling(pkg);
    setOutput(null);
    try {
      const res = await api.uninstallPackage(pkg);
      message.success(t('packages.uninstallSuccess', { pkg }));
      setOutput(res.output);
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('packages.uninstallFailed'));
    } finally {
      setUninstalling(null);
    }
  };

  const columns = [
    {
      title: t('packages.colName'),
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => <Text style={{ color: 'var(--jf-text)', fontFamily: 'monospace' }}>{name}</Text>,
    },
    {
      title: t('packages.colVersion'),
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
        <Popconfirm title={t('packages.uninstallConfirm', { pkg: record.name })} onConfirm={() => handleUninstall(record.name)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
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
    <div style={{
      padding: isMobile ? '16px 12px 24px' : '24px 32px',
      paddingLeft: isMobile ? 52 : undefined,
      maxWidth: 960, margin: '0 auto', width: '100%',
    }}>
      <Space align="center" size={10} style={{ marginBottom: 6, flexWrap: 'wrap' }}>
        <Package size={22} weight="duotone" color="var(--jf-accent)" />
        <Text strong style={{ fontSize: 16, color: 'var(--jf-text)' }}>{t('packages.title')}</Text>
        {venvReady ? (
          <Tag color="green" style={{ fontSize: 10 }}>{t('packages.ready')}</Tag>
        ) : (
          <Tag color="orange" style={{ fontSize: 10 }}>{t('packages.notReady')}</Tag>
        )}
      </Space>
      <Paragraph style={{ color: 'var(--jf-text-muted)', fontSize: 13, marginBottom: 16 }}>
        {t('packages.intro')}
      </Paragraph>

      {!venvReady && (
        <div style={{
          background: 'var(--jf-bg-raised)', borderRadius: 'var(--jf-radius-lg)', padding: 24, marginBottom: 16,
          textAlign: 'center', border: '1px solid var(--jf-border)',
        }}>
          <Package size={40} weight="duotone" color="var(--jf-accent)" style={{ marginBottom: 12 }} />
          <Paragraph style={{ color: 'var(--jf-text-muted)', marginBottom: 16 }}>
            {t('packages.initFirstHint')}
          </Paragraph>
          <Button
            type="primary"
            icon={<Plus size={16} />}
            loading={initializing}
            onClick={handleInit}
            style={{ background: 'var(--jf-accent)', borderColor: 'var(--jf-accent)' }}
          >
            {t('packages.initBtn')}
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
              <Trans i18nKey="packages.installLabel">
                Install a Python package (version pin works, e.g. <code style={{ color: 'var(--jf-accent)' }}>requests==2.31.0</code>)
              </Trans>
            </Text>
            <Space.Compact style={{ width: '100%' }}>
              <Input
                value={newPkg}
                onChange={(e) => setNewPkg(e.target.value)}
                placeholder={t('packages.installPlaceholder')}
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
                {t('packages.installBtn')}
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

          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: isMobile ? 'stretch' : 'center',
            marginBottom: 8, gap: 12,
            flexDirection: isMobile ? 'column' : 'row',
          }}>
            <Input
              prefix={<MagnifyingGlass size={14} color="var(--jf-text-dim)" />}
              placeholder={t('packages.searchPlaceholder')}
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
              allowClear
              style={{
                flex: 1,
                maxWidth: isMobile ? '100%' : 300,
                background: 'var(--jf-bg-deep)',
                border: '1px solid var(--jf-border)',
                color: 'var(--jf-text)',
              }}
            />
            <Space size={8}>
              <Text style={{ color: 'var(--jf-text-muted)', fontSize: 12, whiteSpace: 'nowrap' }}>
                {searchTerm.trim() ? `${filteredPackages.length} / ${packages.length}` : t('packages.countLabel', { count: packages.length })}
              </Text>
              <Button
                size="small" type="text"
                icon={<ArrowsClockwise size={14} />}
                onClick={load}
                style={{ color: 'var(--jf-text-muted)' }}
              >
                {t('common.refresh')}
              </Button>
            </Space>
          </div>

          <Spin spinning={loading}>
            <Table
              dataSource={filteredPackages}
              columns={columns}
              rowKey="name"
              size="small"
              pagination={{ pageSize: 20, showSizeChanger: false, simple: isMobile }}
              scroll={isMobile ? { x: 'max-content' } : undefined}
              style={{ background: 'var(--jf-bg-raised)', borderRadius: 'var(--jf-radius-md)' }}
            />
          </Spin>
        </>
      )}
    </div>
  );
}

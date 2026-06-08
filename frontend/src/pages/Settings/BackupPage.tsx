import { Fragment, useEffect, useMemo, useState } from 'react';
import {
  Typography, Checkbox, Switch, Button, Tooltip, Alert, Modal, Input,
  message, Spin, Tag, Upload, Radio,
} from 'antd';
import {
  Archive, DownloadSimple, UploadSimple, ShieldWarning,
  Question, FileZip, Info, Warning,
} from '@phosphor-icons/react';
import { useTranslation, Trans } from 'react-i18next';
import * as api from '../../services/api';
import { useIsMobile } from '../../hooks/useMediaQuery';

const C = {
  bg2: 'var(--jf-bg-raised)',
  text: 'var(--jf-text)',
  muted: 'var(--jf-text-muted)',
  primary: 'var(--jf-accent)',
  border: 'var(--jf-border)',
  warning: 'var(--jf-warning)',
  error: 'var(--jf-error)',
  success: 'var(--jf-success)',
};

function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

const HELP_KEYS: Record<string, string> = {
  filesystem:    'backup.modHelpFilesystem',
  conversations: 'backup.modHelpConversations',
  services:      'backup.modHelpServices',
  tasks:         'backup.modHelpTasks',
  settings:      'backup.modHelpSettings',
  api_keys:      'backup.modHelpApiKeys',
};

export default function BackupPage() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [modules, setModules] = useState<api.BackupModule[]>([]);
  const [defaultSelected, setDefaultSelected] = useState<string[]>([]);
  const [selectedExport, setSelectedExport] = useState<string[]>([]);
  const [includeMedia, setIncludeMedia] = useState(true);
  const [includeApiKeys, setIncludeApiKeys] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<api.BackupPreviewResp | null>(null);
  const [exporting, setExporting] = useState(false);

  // Import state
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importMode, setImportMode] = useState<'merge' | 'overwrite'>('merge');
  const [importPwd, setImportPwd] = useState('');
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<api.BackupImportResp | null>(null);

  useEffect(() => {
    api.listBackupModules().then(r => {
      setModules(r.modules);
      setDefaultSelected(r.default_selected);
      setSelectedExport(r.default_selected);
    }).catch(e => message.error(t('backup.loadModulesFailed', { err: String(e) })));
  }, [t]);

  const allModuleIds = useMemo(() => modules.map(m => m.id), [modules]);

  const handlePreview = async () => {
    if (selectedExport.length === 0) {
      message.warning(t('backup.selectAtLeastOne'));
      return;
    }
    setPreviewing(true);
    try {
      const r = await api.previewBackup({
        modules: selectedExport,
        includeMedia,
        includeApiKeys,
      });
      setPreview(r);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('backup.previewFailed'));
    }
    setPreviewing(false);
  };

  const handleExport = async () => {
    if (selectedExport.length === 0) {
      message.warning(t('backup.selectAtLeastOne'));
      return;
    }
    if (includeApiKeys) {
      const ok = await new Promise<boolean>(resolve => {
        Modal.confirm({
          title: t('backup.exportConfirmTitle'),
          content: t('backup.exportConfirmDesc'),
          okText: t('backup.confirmExportBtn'), cancelText: t('common.cancel'), okButtonProps: { danger: true },
          onOk: () => resolve(true), onCancel: () => resolve(false),
        });
      });
      if (!ok) return;
    }
    setExporting(true);
    try {
      const r = await api.downloadBackup({
        modules: selectedExport,
        includeMedia,
        includeApiKeys,
      });
      message.success(t('backup.exportSuccess', { filename: r.filename, size: fmtBytes(r.sizeBytes), count: r.fileCount }));
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('backup.exportFailed'));
    }
    setExporting(false);
  };

  const handleImport = async () => {
    if (!importFile) {
      message.warning(t('backup.selectZipFirst'));
      return;
    }
    if (importMode === 'overwrite' && !importPwd) {
      message.warning(t('backup.needPwd'));
      return;
    }
    if (importMode === 'overwrite') {
      const ok = await new Promise<boolean>(resolve => {
        Modal.confirm({
          title: t('backup.dangerOverwriteTitle'),
          content: (
            <div>
              <p>{t('backup.dangerOverwrite1')}</p>
              <p>
                <Trans i18nKey="backup.dangerOverwrite2">
                  Old files are moved to <code>users/&lt;your-id&gt;.pre-restore-&lt;ts&gt;/</code> as a snapshot — manual recovery is possible.
                </Trans>
              </p>
              <p>{t('backup.dangerOverwrite3')}</p>
            </div>
          ),
          okText: t('backup.dangerOverwriteOk'), cancelText: t('common.cancel'),
          okButtonProps: { danger: true },
          onOk: () => resolve(true), onCancel: () => resolve(false),
        });
      });
      if (!ok) return;
    }
    setImporting(true);
    setImportResult(null);
    try {
      const r = await api.importBackup({
        file: importFile,
        mode: importMode,
        password: importPwd || undefined,
      });
      setImportResult(r);
      message.success(t('backup.importSuccess', { count: r.files_written }));
      setImportPwd('');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : t('backup.importFailed'));
    }
    setImporting(false);
  };

  const cardStyle: React.CSSProperties = {
    background: C.bg2,
    borderRadius: 'var(--jf-radius-lg)',
    border: `1px solid ${C.border}`,
    padding: isMobile ? '16px 14px' : '20px 24px',
    marginBottom: 16,
  };

  return (
    <div style={{
      padding: isMobile ? '16px 12px 24px' : '24px 32px',
      paddingLeft: isMobile ? 52 : undefined,
      maxWidth: 960, margin: '0 auto', width: '100%',
    }}>
      <Typography.Text style={{ color: C.text, fontSize: 18, fontWeight: 600, display: 'block', marginBottom: 8 }}>
        {t('backup.pageTitle')}
      </Typography.Text>
      <Typography.Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 20 }}>
        {t('backup.pageDesc')}
      </Typography.Text>

      {/* Export Card */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <DownloadSimple size={18} color={C.primary} />
          <Typography.Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>
            {t('backup.exportTitle')}
          </Typography.Text>
          <Tooltip title={t('backup.exportTip')}>
            <Question size={14} color={C.muted} style={{ cursor: 'help' }} />
          </Tooltip>
        </div>

        <Typography.Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 12 }}>
          {t('backup.checkPrompt')}
        </Typography.Text>

        <Checkbox.Group
          value={selectedExport}
          onChange={(vals) => { setSelectedExport(vals as string[]); setPreview(null); }}
          style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}
        >
          {modules.map(m => (
            <div key={m.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <Checkbox value={m.id}>
                <span style={{ color: C.text, fontSize: 13 }}>{m.label}</span>
              </Checkbox>
              <Tooltip title={HELP_KEYS[m.id] ? t(HELP_KEYS[m.id]) : ''}>
                <Question size={13} color={C.muted} style={{ marginTop: 4, cursor: 'help' }} />
              </Tooltip>
              {m.id === 'api_keys' && selectedExport.includes('api_keys') && (
                <Tag color="warning" style={{ marginLeft: 4, fontSize: 10 }}>{t('backup.plaintextTag')}</Tag>
              )}
            </div>
          ))}
        </Checkbox.Group>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 12, paddingTop: 12, borderTop: `1px dashed ${C.border}` }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <Typography.Text style={{ color: C.text, fontSize: 13 }}>{t('backup.includeMedia')}</Typography.Text>
              <Tooltip title={t('backup.includeMediaTip')}>
                <Question size={13} color={C.muted} style={{ marginLeft: 6, cursor: 'help' }} />
              </Tooltip>
            </div>
            <Switch checked={includeMedia} onChange={(v) => { setIncludeMedia(v); setPreview(null); }} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <Typography.Text style={{ color: C.text, fontSize: 13 }}>{t('backup.exportApiKeys')}</Typography.Text>
              <Tooltip title={t('backup.exportApiKeysTip')}>
                <Question size={13} color={C.muted} style={{ marginLeft: 6, cursor: 'help' }} />
              </Tooltip>
            </div>
            <Switch checked={includeApiKeys} onChange={(v) => { setIncludeApiKeys(v); setPreview(null); }} />
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <Button
            icon={<Info size={14} />}
            onClick={handlePreview}
            loading={previewing}
            size="small"
          >
            {t('backup.estimateBtn')}
          </Button>
          <Button
            type="primary"
            icon={<Archive size={14} />}
            onClick={handleExport}
            loading={exporting}
          >
            {t('backup.exportBtn')}
          </Button>
          <Button
            size="small"
            onClick={() => { setSelectedExport(allModuleIds); setIncludeApiKeys(true); setPreview(null); }}
          >
            {t('backup.selectAll')}
          </Button>
          <Button
            size="small"
            onClick={() => { setSelectedExport(defaultSelected); setIncludeApiKeys(false); setPreview(null); }}
          >
            {t('backup.resetDefault')}
          </Button>
        </div>

        {preview && (
          <div style={{ marginTop: 14, padding: 12, background: 'var(--jf-bg-deep)', borderRadius: 8, border: `1px solid ${C.border}`, overflowX: 'auto' }}>
            <Typography.Text style={{ color: C.muted, fontSize: 11, display: 'block', marginBottom: 6 }}>
              {t('backup.estimateLabel')}
            </Typography.Text>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '4px 12px', fontSize: 12, minWidth: isMobile ? 320 : 'unset' }}>
              {Object.entries(preview.modules).map(([mod, info]) => (
                <Fragment key={mod}>
                  <Typography.Text style={{ color: C.text }}>{mod}</Typography.Text>
                  <Typography.Text style={{ color: C.muted, fontFamily: 'monospace' }}>
                    {info.file_count} files
                  </Typography.Text>
                  <Typography.Text style={{ color: C.muted, fontFamily: 'monospace', textAlign: 'right' }}>
                    {fmtBytes(info.total_bytes)}
                  </Typography.Text>
                </Fragment>
              ))}
              <Typography.Text strong style={{ color: C.text, borderTop: `1px solid ${C.border}`, paddingTop: 4 }}>
                {t('backup.totalLabel')}
              </Typography.Text>
              <Typography.Text strong style={{ color: C.text, borderTop: `1px solid ${C.border}`, paddingTop: 4, fontFamily: 'monospace' }}>
                {preview.total_file_count} files
              </Typography.Text>
              <Typography.Text strong style={{ color: C.primary, borderTop: `1px solid ${C.border}`, paddingTop: 4, fontFamily: 'monospace', textAlign: 'right' }}>
                {fmtBytes(preview.total_uncompressed_bytes)}
              </Typography.Text>
            </div>
            <Typography.Text style={{ color: C.muted, fontSize: 10, display: 'block', marginTop: 6 }}>
              {t('backup.zipNote')}
            </Typography.Text>
          </div>
        )}
      </div>

      {/* Import Card */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <UploadSimple size={18} color={C.primary} />
          <Typography.Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>
            {t('backup.importTitle')}
          </Typography.Text>
          <Tooltip title={t('backup.importTip')}>
            <Question size={14} color={C.muted} style={{ cursor: 'help' }} />
          </Tooltip>
        </div>

        <Alert
          showIcon
          type="info"
          message={t('backup.importNotice')}
          style={{ marginBottom: 14 }}
        />

        <Upload.Dragger
          accept=".zip,application/zip"
          beforeUpload={(f) => { setImportFile(f); setImportResult(null); return false; }}
          maxCount={1}
          fileList={importFile ? [{ uid: '-1', name: importFile.name, status: 'done', size: importFile.size } as never] : []}
          onRemove={() => { setImportFile(null); setImportResult(null); }}
          style={{ background: 'var(--jf-bg-deep)', borderColor: C.border, marginBottom: 14 }}
        >
          <p style={{ marginBottom: 6 }}>
            <FileZip size={32} color={C.primary} />
          </p>
          <p style={{ color: C.text, fontSize: 13 }}>{t('backup.importDragText')}</p>
          <p style={{ color: C.muted, fontSize: 11 }}>{t('backup.importDragHint')}</p>
        </Upload.Dragger>

        <div style={{ marginBottom: 14 }}>
          <Typography.Text style={{ color: C.text, fontSize: 13, display: 'block', marginBottom: 8 }}>
            {t('backup.importMode')}
          </Typography.Text>
          <Radio.Group
            value={importMode}
            onChange={(e) => setImportMode(e.target.value)}
            style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
          >
            <Radio value="merge">
              <span style={{ color: C.text, fontSize: 13 }}>{t('backup.modeMerge')}</span>
              <Typography.Text style={{ color: C.muted, fontSize: 11, display: 'block', marginLeft: 22 }}>
                {t('backup.modeMergeDesc')}
              </Typography.Text>
            </Radio>
            <Radio value="overwrite">
              <span style={{ color: C.text, fontSize: 13 }}>{t('backup.modeOverwrite')}</span>
              <Typography.Text style={{ color: C.muted, fontSize: 11, display: 'block', marginLeft: 22 }}>
                <Trans i18nKey="backup.modeOverwriteDesc">
                  Modules in the ZIP fully replace current data; old files are moved to <code>.pre-restore-&lt;time&gt;/</code> as a snapshot.
                </Trans>
                <ShieldWarning size={12} color={C.warning} style={{ verticalAlign: 'middle', marginLeft: 4 }} />
                {t('backup.needPwdConfirm')}
              </Typography.Text>
            </Radio>
          </Radio.Group>
        </div>

        {importMode === 'overwrite' && (
          <div style={{ marginBottom: 14 }}>
            <Typography.Text style={{ color: C.text, fontSize: 12, display: 'block', marginBottom: 6 }}>
              {t('backup.passwordPrompt')}
            </Typography.Text>
            <Input.Password
              value={importPwd}
              onChange={(e) => setImportPwd(e.target.value)}
              placeholder={t('backup.passwordPlaceholder')}
              style={{ maxWidth: 320 }}
            />
          </div>
        )}

        <Button
          type="primary"
          danger={importMode === 'overwrite'}
          icon={<UploadSimple size={14} />}
          onClick={handleImport}
          loading={importing}
          disabled={!importFile}
        >
          {importMode === 'overwrite' ? t('backup.startOverwrite') : t('backup.startMerge')}
        </Button>

        {importing && <Spin style={{ marginLeft: 12 }} />}

        {importResult && (
          <Alert
            style={{ marginTop: 14 }}
            showIcon
            type="success"
            message={
              <span>
                <Trans
                  i18nKey="backup.importDetail"
                  values={{
                    mode: importResult.mode,
                    written: importResult.files_written,
                    skipped: importResult.files_skipped,
                    keys: importResult.api_keys_imported,
                  }}
                >
                  Import done (mode: {importResult.mode}) — wrote <strong>{importResult.files_written}</strong> files, skipped <strong>{importResult.files_skipped}</strong>, re-encrypted <strong>{importResult.api_keys_imported}</strong> API keys.
                </Trans>
              </span>
            }
            description={
              <div style={{ fontSize: 11, marginTop: 4 }}>
                {importResult.snapshot_path && (
                  <div>
                    <Warning size={12} style={{ verticalAlign: 'middle', color: C.warning }} />{' '}
                    {t('backup.snapshotTo')} <code>{importResult.snapshot_path}</code>
                  </div>
                )}
                {importResult.warnings.length > 0 && (
                  <div style={{ marginTop: 4, color: C.warning }}>
                    {t('backup.warningsLabel', { warnings: importResult.warnings.join('；') })}
                  </div>
                )}
                <div style={{ marginTop: 4, color: C.muted }}>
                  {t('backup.refreshHint')}
                </div>
              </div>
            }
          />
        )}
      </div>
    </div>
  );
}

import { Fragment, useEffect, useMemo, useState } from 'react';
import {
  Typography, Checkbox, Switch, Button, Tooltip, Alert, Modal, Input,
  message, Spin, Tag, Upload, Radio,
} from 'antd';
import {
  Archive, DownloadSimple, UploadSimple, ShieldWarning,
  Question, FileZip, Info, Warning,
} from '@phosphor-icons/react';
import * as api from '../../services/api';

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

const HELP_TEXTS: Record<string, string> = {
  filesystem:    '你的整个 /docs、/scripts、/generated、/soul 文件系统。Agent 工作产物都在这里。',
  conversations: '所有 Admin 对话历史 + 每条会话的附件、生成图。可能很大。',
  services:      '已发布的 Service 配置 + 每个 Service 自己的对话和定时任务。',
  tasks:         'Admin 视角下的所有定时任务（cron / 一次性）。',
  settings:      '系统提示词、用户档案、Subagent 配置、能力提示、偏好设置、Soul 元数据 等。',
  api_keys:      '⚠ 导出为明文 JSON（备份要私密保管）。导入时会重新加密为本机的 ENC: 密文。',
};

export default function BackupPage() {
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
    }).catch(e => message.error('加载模块失败: ' + e));
  }, []);

  const allModuleIds = useMemo(() => modules.map(m => m.id), [modules]);

  const handlePreview = async () => {
    if (selectedExport.length === 0) {
      message.warning('请至少选择一个模块');
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
      message.error(e instanceof Error ? e.message : '预览失败');
    }
    setPreviewing(false);
  };

  const handleExport = async () => {
    if (selectedExport.length === 0) {
      message.warning('请至少选择一个模块');
      return;
    }
    if (includeApiKeys) {
      const ok = await new Promise<boolean>(resolve => {
        Modal.confirm({
          title: '确认导出 API Keys？',
          content: '导出 ZIP 内将包含明文 API Keys（仅在你完全信任备份去向时勾选）。继续？',
          okText: '确认导出', cancelText: '取消', okButtonProps: { danger: true },
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
      message.success(`已生成 ${r.filename}（${fmtBytes(r.sizeBytes)}, ${r.fileCount} 个文件）`);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '导出失败');
    }
    setExporting(false);
  };

  const handleImport = async () => {
    if (!importFile) {
      message.warning('请先选择 ZIP 文件');
      return;
    }
    if (importMode === 'overwrite' && !importPwd) {
      message.warning('覆盖模式需要输入登录密码确认');
      return;
    }
    if (importMode === 'overwrite') {
      const ok = await new Promise<boolean>(resolve => {
        Modal.confirm({
          title: '⚠ 危险操作：覆盖模式',
          content: (
            <div>
              <p>覆盖模式会清空你当前选中模块的所有文件，然后用 ZIP 内的内容替换。</p>
              <p>原文件会被移动到 <code>users/&lt;你的id&gt;.pre-restore-&lt;时间戳&gt;/</code> 作为快照保存，可手动恢复。</p>
              <p>确定要继续吗？</p>
            </div>
          ),
          okText: '我已了解，开始覆盖', cancelText: '取消',
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
      message.success(`导入完成：写入 ${r.files_written} 个文件`);
      setImportPwd('');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '导入失败');
    }
    setImporting(false);
  };

  const cardStyle = {
    background: C.bg2,
    borderRadius: 'var(--jf-radius-lg)',
    border: `1px solid ${C.border}`,
    padding: '20px 24px',
    marginBottom: 16,
  } as const;

  return (
    <div style={{ padding: '24px 32px', maxWidth: 960, margin: '0 auto', width: '100%' }}>
      <Typography.Text style={{ color: C.text, fontSize: 18, fontWeight: 600, display: 'block', marginBottom: 8 }}>
        数据备份与恢复
      </Typography.Text>
      <Typography.Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 20 }}>
        每个用户可备份/恢复自己的全部数据。备份不会跨用户、不会包含其他人的数据。
      </Typography.Text>

      {/* Export Card */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <DownloadSimple size={18} color={C.primary} />
          <Typography.Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>
            导出 (Export)
          </Typography.Text>
          <Tooltip title="选择要打包的模块，生成一份 ZIP 备份并下载到本地。可用于迁移到新机器或定期备份。">
            <Question size={14} color={C.muted} style={{ cursor: 'help' }} />
          </Tooltip>
        </div>

        <Typography.Text style={{ color: C.muted, fontSize: 12, display: 'block', marginBottom: 12 }}>
          勾选要打包的模块（默认包含除 API Keys 外的所有内容）：
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
              <Tooltip title={HELP_TEXTS[m.id] || ''}>
                <Question size={13} color={C.muted} style={{ marginTop: 4, cursor: 'help' }} />
              </Tooltip>
              {m.id === 'api_keys' && selectedExport.includes('api_keys') && (
                <Tag color="warning" style={{ marginLeft: 4, fontSize: 10 }}>明文输出</Tag>
              )}
            </div>
          ))}
        </Checkbox.Group>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 12, paddingTop: 12, borderTop: `1px dashed ${C.border}` }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <Typography.Text style={{ color: C.text, fontSize: 13 }}>包含媒体文件</Typography.Text>
              <Tooltip title="包含 generated/ 和 query_appendix/images/ 内的图片、音频、视频。关闭可以让备份小很多，但聊天记录里的引用图就丢了。">
                <Question size={13} color={C.muted} style={{ marginLeft: 6, cursor: 'help' }} />
              </Tooltip>
            </div>
            <Switch checked={includeMedia} onChange={(v) => { setIncludeMedia(v); setPreview(null); }} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <Typography.Text style={{ color: C.text, fontSize: 13 }}>导出 API Keys（明文）</Typography.Text>
              <Tooltip title="勾选后 ZIP 内会包含一个 api_keys.PLAINTEXT.json，导入到新机器时会被重新加密。请妥善保管备份文件。">
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
            预估大小
          </Button>
          <Button
            type="primary"
            icon={<Archive size={14} />}
            onClick={handleExport}
            loading={exporting}
          >
            导出 ZIP
          </Button>
          <Button
            size="small"
            onClick={() => { setSelectedExport(allModuleIds); setIncludeApiKeys(true); setPreview(null); }}
          >
            全选
          </Button>
          <Button
            size="small"
            onClick={() => { setSelectedExport(defaultSelected); setIncludeApiKeys(false); setPreview(null); }}
          >
            重置默认
          </Button>
        </div>

        {preview && (
          <div style={{ marginTop: 14, padding: 12, background: 'var(--jf-bg-deep)', borderRadius: 8, border: `1px solid ${C.border}` }}>
            <Typography.Text style={{ color: C.muted, fontSize: 11, display: 'block', marginBottom: 6 }}>
              预估（未压缩）：
            </Typography.Text>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '4px 12px', fontSize: 12 }}>
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
                合计
              </Typography.Text>
              <Typography.Text strong style={{ color: C.text, borderTop: `1px solid ${C.border}`, paddingTop: 4, fontFamily: 'monospace' }}>
                {preview.total_file_count} files
              </Typography.Text>
              <Typography.Text strong style={{ color: C.primary, borderTop: `1px solid ${C.border}`, paddingTop: 4, fontFamily: 'monospace', textAlign: 'right' }}>
                {fmtBytes(preview.total_uncompressed_bytes)}
              </Typography.Text>
            </div>
            <Typography.Text style={{ color: C.muted, fontSize: 10, display: 'block', marginTop: 6 }}>
              ZIP 压缩后通常为此大小的 30%~70%（媒体文件压缩率较低）。
            </Typography.Text>
          </div>
        )}
      </div>

      {/* Import Card */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <UploadSimple size={18} color={C.primary} />
          <Typography.Text style={{ color: C.text, fontSize: 14, fontWeight: 500 }}>
            导入 (Import)
          </Typography.Text>
          <Tooltip title="上传一份之前导出的 ZIP，恢复或合并到当前账号。仅会影响你自己的数据。">
            <Question size={14} color={C.muted} style={{ cursor: 'help' }} />
          </Tooltip>
        </div>

        <Alert
          showIcon
          type="info"
          message="导入操作仅影响当前登录用户的数据，不会触及其他用户。"
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
          <p style={{ color: C.text, fontSize: 13 }}>点击或拖拽备份 ZIP 到此处</p>
          <p style={{ color: C.muted, fontSize: 11 }}>仅支持本应用导出的 ZIP（含 _jellyfishbot_backup.json 清单）</p>
        </Upload.Dragger>

        <div style={{ marginBottom: 14 }}>
          <Typography.Text style={{ color: C.text, fontSize: 13, display: 'block', marginBottom: 8 }}>
            导入模式
          </Typography.Text>
          <Radio.Group
            value={importMode}
            onChange={(e) => setImportMode(e.target.value)}
            style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
          >
            <Radio value="merge">
              <span style={{ color: C.text, fontSize: 13 }}>合并 (Merge)</span>
              <Typography.Text style={{ color: C.muted, fontSize: 11, display: 'block', marginLeft: 22 }}>
                只补充缺失的文件，不覆盖任何现有内容。安全。
              </Typography.Text>
            </Radio>
            <Radio value="overwrite">
              <span style={{ color: C.text, fontSize: 13 }}>覆盖 (Overwrite / Restore)</span>
              <Typography.Text style={{ color: C.muted, fontSize: 11, display: 'block', marginLeft: 22 }}>
                把 ZIP 内的模块完全替换当前数据；旧文件会被移动到 <code>.pre-restore-&lt;时间&gt;/</code> 快照目录。
                <ShieldWarning size={12} color={C.warning} style={{ verticalAlign: 'middle', marginLeft: 4 }} />
                需要密码确认。
              </Typography.Text>
            </Radio>
          </Radio.Group>
        </div>

        {importMode === 'overwrite' && (
          <div style={{ marginBottom: 14 }}>
            <Typography.Text style={{ color: C.text, fontSize: 12, display: 'block', marginBottom: 6 }}>
              请输入当前账号密码确认：
            </Typography.Text>
            <Input.Password
              value={importPwd}
              onChange={(e) => setImportPwd(e.target.value)}
              placeholder="登录密码"
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
          {importMode === 'overwrite' ? '开始覆盖恢复' : '开始合并导入'}
        </Button>

        {importing && <Spin style={{ marginLeft: 12 }} />}

        {importResult && (
          <Alert
            style={{ marginTop: 14 }}
            showIcon
            type="success"
            message={
              <span>
                导入完成（模式: {importResult.mode}）— 写入 <strong>{importResult.files_written}</strong> 个文件，
                跳过 <strong>{importResult.files_skipped}</strong> 个，
                重新加密 API Key <strong>{importResult.api_keys_imported}</strong> 项。
              </span>
            }
            description={
              <div style={{ fontSize: 11, marginTop: 4 }}>
                {importResult.snapshot_path && (
                  <div>
                    <Warning size={12} style={{ verticalAlign: 'middle', color: C.warning }} />{' '}
                    旧数据已快照到：<code>{importResult.snapshot_path}</code>
                  </div>
                )}
                {importResult.warnings.length > 0 && (
                  <div style={{ marginTop: 4, color: C.warning }}>
                    警告：{importResult.warnings.join('；')}
                  </div>
                )}
                <div style={{ marginTop: 4, color: C.muted }}>
                  建议刷新页面以加载新数据。
                </div>
              </div>
            }
          />
        )}
      </div>
    </div>
  );
}

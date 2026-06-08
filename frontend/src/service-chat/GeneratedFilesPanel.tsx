/**
 * GeneratedFilesPanel — 消费者侧「本会话生成文件」抽屉。
 *
 * 调 GET /api/v1/conversations/{conv_id}/files 列出本会话 generated/ 下的全部文件，
 * 每条提供：媒体缩略图（图片）/类型图标、文件名、大小、下载按钮。
 * 下载/缩略图 URL 用会话级短期 media token 鉴权（不暴露 sk-svc- 主 key）。
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { listGeneratedFiles, type GeneratedFile } from './serviceApi';
import styles from './serviceChat.module.css';

interface Props {
  apiKey: string;
  convId: string;
  /** 构造带 token 的文件 URL；download=true 时强制附件下载 */
  buildUrl: (filePath: string, opts?: { download?: boolean }) => string;
  onClose: () => void;
}

const IMAGE_EXTS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'];

function extOf(path: string): string {
  const i = path.lastIndexOf('.');
  return i >= 0 ? path.slice(i).toLowerCase() : '';
}

function iconFor(path: string): string {
  const ext = extOf(path);
  if (['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac'].includes(ext)) return '🎵';
  if (['.mp4', '.webm', '.mov', '.avi', '.mkv'].includes(ext)) return '🎬';
  if (ext === '.pdf') return '📕';
  if (['.csv', '.xlsx', '.xls'].includes(ext)) return '📊';
  if (['.zip', '.tar', '.gz', '.7z'].includes(ext)) return '🗜️';
  if (['.html', '.htm'].includes(ext)) return '🌐';
  if (['.json', '.txt', '.md', '.py', '.js', '.ts'].includes(ext)) return '📄';
  return '📎';
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default function GeneratedFilesPanel({ apiKey, convId, buildUrl, onClose }: Props) {
  const { t } = useTranslation();
  const [files, setFiles] = useState<GeneratedFile[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await listGeneratedFiles(apiKey, convId);
      setFiles(list);
    } catch (err) {
      console.error('list generated files failed:', err);
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, [apiKey, convId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className={styles.filesOverlay} onClick={onClose}>
      <div className={styles.filesDrawer} onClick={(e) => e.stopPropagation()}>
        <div className={styles.filesDrawerHeader}>
          <span className={styles.filesDrawerTitle}>
            {t('service.filesTitle', '本会话生成文件')}
          </span>
          <button
            type="button"
            className={styles.filesDrawerRefresh}
            onClick={() => void load()}
            title={t('service.filesRefresh', '刷新')}
          >
            ⟳
          </button>
          <button
            type="button"
            className={styles.filesDrawerClose}
            onClick={onClose}
            aria-label={t('service.filesClose', '关闭')}
          >
            ×
          </button>
        </div>

        <div className={styles.filesList}>
          {loading && <div className={styles.filesEmpty}>{t('service.filesLoading', '加载中…')}</div>}
          {!loading && files.length === 0 && (
            <div className={styles.filesEmpty}>{t('service.filesEmpty', '暂无生成文件')}</div>
          )}
          {!loading &&
            files.map((f) => {
              const isImg = IMAGE_EXTS.includes(extOf(f.path));
              const previewUrl = buildUrl('/generated/' + f.path);
              const downloadUrl = buildUrl('/generated/' + f.path, { download: true });
              const name = f.path.split('/').pop() || f.path;
              return (
                <div key={f.path} className={styles.fileRow}>
                  {isImg ? (
                    <img className={styles.fileThumb} src={previewUrl} alt={name} loading="lazy" />
                  ) : (
                    <div className={styles.fileIcon}>{iconFor(f.path)}</div>
                  )}
                  <div className={styles.fileMeta}>
                    <div className={styles.fileName} title={f.path}>{name}</div>
                    <div className={styles.fileSize}>{fmtSize(f.size)}</div>
                  </div>
                  <a className={styles.fileDownload} href={downloadUrl} download={name}>
                    {t('service.filesDownload', '下载')}
                  </a>
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
}

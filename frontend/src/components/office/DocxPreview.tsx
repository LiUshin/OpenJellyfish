import { useEffect, useRef, useState } from 'react';
import { Empty, Spin } from 'antd';
import { renderAsync } from 'docx-preview';
import type { OfficePreviewProps } from './types';

const C = {
  bg: 'var(--jf-bg-panel)',
  bgDark: 'var(--jf-bg-deep)',
  textSec: 'var(--jf-text-muted)',
  textDim: 'var(--jf-text-dim)',
};

/**
 * .docx 纯前端预览（docx-preview）。
 * 不支持旧版 .doc；复杂 SmartArt / 部分嵌入对象会降级。
 */
export default function DocxPreview({ getArrayBuffer, fileName }: OfficePreviewProps) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const styleRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setLoading(true);
      setError(null);
      try {
        const buf = await getArrayBuffer();
        if (cancelled || !bodyRef.current) return;
        bodyRef.current.innerHTML = '';
        if (styleRef.current) styleRef.current.innerHTML = '';
        await renderAsync(buf, bodyRef.current, styleRef.current || undefined, {
          className: 'jf-docx',
          inWrapper: true,
          ignoreWidth: false,
          breakPages: true,
          renderHeaders: true,
          renderFooters: true,
          useBase64URL: true,
        });
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [getArrayBuffer]);

  if (error) {
    return (
      <div style={centerStyle}>
        <Empty
          description={
            <div style={{ color: C.textSec, fontSize: 13 }}>
              Word 预览失败
              <div style={{ fontSize: 11, color: C.textDim, marginTop: 4 }}>
                {fileName ? `${fileName} · ` : ''}{error}
              </div>
            </div>
          }
        />
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, background: C.bgDark }}>
      {loading && (
        <div style={{ ...centerStyle, position: 'absolute', inset: 0, zIndex: 2, background: C.bgDark }}>
          <Spin tip="正在渲染 Word…" />
        </div>
      )}
      <div ref={styleRef} style={{ display: 'none' }} />
      <div
        ref={bodyRef}
        className="jf-docx-preview"
        style={{
          flex: 1,
          overflow: 'auto',
          padding: 16,
          background: C.bg,
          minHeight: 0,
        }}
      />
    </div>
  );
}

const centerStyle: React.CSSProperties = {
  flex: 1,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: C.bgDark,
  padding: 24,
};

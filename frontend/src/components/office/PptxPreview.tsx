import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Empty, Spin } from 'antd';
import { LeftOutlined, RightOutlined } from '@ant-design/icons';
import { PPTXViewer } from 'pptxviewjs';
import type { OfficePreviewProps } from './types';

const C = {
  bgDark: 'var(--jf-bg-deep)',
  text: 'var(--jf-text)',
  textSec: 'var(--jf-text-muted)',
  textDim: 'var(--jf-text-dim)',
  border: 'var(--jf-border)',
};

/**
 * .pptx 纯前端预览（PptxViewJS Canvas）。
 * 只读翻页；不支持旧版 .ppt；动画/SmartArt 可能降级。
 */
export default function PptxPreview({ getArrayBuffer, fileName }: OfficePreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<PPTXViewer | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [slideIndex, setSlideIndex] = useState(0);
  const [slideCount, setSlideCount] = useState(0);

  const syncMeta = useCallback((viewer: PPTXViewer) => {
    setSlideCount(viewer.getSlideCount());
    setSlideIndex(viewer.getCurrentSlideIndex());
  }, []);

  useEffect(() => {
    let cancelled = false;
    let viewer: PPTXViewer | null = null;

    const run = async () => {
      setLoading(true);
      setError(null);
      setSlideCount(0);
      setSlideIndex(0);
      try {
        const buf = await getArrayBuffer();
        if (cancelled || !canvasRef.current) return;
        viewer = new PPTXViewer({
          canvas: canvasRef.current,
          slideSizeMode: 'fit',
          backgroundColor: '#ffffff',
        });
        viewerRef.current = viewer;
        await viewer.loadFile(buf);
        if (cancelled) return;
        await viewer.render();
        syncMeta(viewer);
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
      viewerRef.current?.destroy();
      viewerRef.current = null;
      viewer?.destroy();
    };
  }, [getArrayBuffer, syncMeta]);

  // 容器尺寸变化时重渲当前页，避免 split 拖拽后 canvas 留白
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const ro = new ResizeObserver(() => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const v = viewerRef.current;
        if (!v || !canvasRef.current || v.getSlideCount() === 0) return;
        void v.render(canvasRef.current).then(() => syncMeta(v));
      }, 120);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      if (timer) clearTimeout(timer);
    };
  }, [syncMeta]);

  const goPrev = async () => {
    const v = viewerRef.current;
    if (!v || !canvasRef.current) return;
    await v.previousSlide(canvasRef.current);
    syncMeta(v);
  };

  const goNext = async () => {
    const v = viewerRef.current;
    if (!v || !canvasRef.current) return;
    await v.nextSlide(canvasRef.current);
    syncMeta(v);
  };

  if (error) {
    return (
      <div style={centerStyle}>
        <Empty
          description={
            <div style={{ color: C.textSec, fontSize: 13 }}>
              PPT 预览失败
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
    <div
      ref={wrapRef}
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        background: C.bgDark,
        position: 'relative',
      }}
    >
      {loading && (
        <div style={{ ...centerStyle, position: 'absolute', inset: 0, zIndex: 2 }}>
          <Spin tip="正在渲染幻灯片…" />
        </div>
      )}
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'auto',
          padding: 12,
          minHeight: 0,
        }}
      >
        <canvas
          ref={canvasRef}
          style={{
            maxWidth: '100%',
            maxHeight: '100%',
            background: '#fff',
            boxShadow: '0 2px 12px rgba(0,0,0,0.18)',
            borderRadius: 4,
          }}
        />
      </div>
      {!loading && slideCount > 0 && (
        <div
          style={{
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 12,
            padding: '8px 12px',
            borderTop: `1px solid ${C.border}`,
            color: C.text,
            fontSize: 12,
          }}
        >
          <Button
            type="text"
            size="small"
            icon={<LeftOutlined />}
            disabled={slideIndex <= 0}
            onClick={() => void goPrev()}
          />
          <span style={{ color: C.textSec, minWidth: 72, textAlign: 'center' }}>
            {slideIndex + 1} / {slideCount}
          </span>
          <Button
            type="text"
            size="small"
            icon={<RightOutlined />}
            disabled={slideIndex >= slideCount - 1}
            onClick={() => void goNext()}
          />
        </div>
      )}
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

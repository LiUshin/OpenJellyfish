import { memo, useEffect, useRef, useState } from 'react';
import type { JellyfishEngine, JellyfishTheme } from './JellyfishEngine';
import styles from './ocean.module.css';

export interface AsciiJellyfishBackgroundProps {
  theme: JellyfishTheme;
  paused?: boolean;
  className?: string;
}

const MOTION_QUERY = '(prefers-reduced-motion: reduce)';

/** Decorative only: chat input and all interactions stay in the foreground. */
function AsciiJellyfishBackground({ theme, paused = false, className }: AsciiJellyfishBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<JellyfishEngine | null>(null);
  const [reducedMotion, setReducedMotion] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(MOTION_QUERY).matches,
  );
  const [rendererState, setRendererState] = useState<'loading' | 'ready' | 'fallback'>('loading');
  const optionsRef = useRef({ theme, paused: paused || reducedMotion });

  useEffect(() => {
    const query = window.matchMedia(MOTION_QUERY);
    const syncPreference = () => setReducedMotion(query.matches);
    syncPreference();
    query.addEventListener('change', syncPreference);
    return () => query.removeEventListener('change', syncPreference);
  }, []);

  useEffect(() => {
    optionsRef.current = { theme, paused: paused || reducedMotion };
    engineRef.current?.update(optionsRef.current);
  }, [theme, paused, reducedMotion]);

  useEffect(() => {
    let disposed = false;
    // Keep the procedural renderer out of the initial chat bundle. A theme or
    // draft change updates the existing instance instead of restarting it.
    void import('./JellyfishEngine').then(({ JellyfishEngine: Engine }) => {
      const canvas = canvasRef.current;
      if (disposed || !canvas) return;
      try {
        engineRef.current = new Engine(canvas, {
          species: 'mixed', speed: 0.8, detail: 8, ...optionsRef.current,
        });
        setRendererState('ready');
      } catch {
        setRendererState('fallback');
      }
    }).catch(() => {
      if (!disposed) setRendererState('fallback');
    });

    return () => {
      disposed = true;
      engineRef.current?.destroy();
      engineRef.current = null;
    };
  }, []);

  return <div
    className={[styles.scene, className].filter(Boolean).join(' ')}
    data-theme={theme}
    data-motion={paused || reducedMotion ? 'paused' : 'playing'}
    data-renderer={rendererState}
    aria-hidden="true"
  >
    <canvas ref={canvasRef} className={styles.canvas} aria-hidden="true" />
  </div>;
}

export default memo(AsciiJellyfishBackground);

import { useRef, useCallback, useEffect } from 'react';

const SCROLL_THRESHOLD = 60;

export function useSmartScroll(isStreaming: boolean) {
  const containerRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  const scrollToBottom = useCallback((force = false) => {
    if (userScrolledUp.current && !force) return;
    const el = containerRef.current;
    if (!el) return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, []);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom <= SCROLL_THRESHOLD) {
      userScrolledUp.current = false;
    } else if (isStreaming) {
      userScrolledUp.current = true;
    }
  }, [isStreaming]);

  const resetScroll = useCallback(() => {
    userScrolledUp.current = false;
    scrollToBottom(true);
  }, [scrollToBottom]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, [handleScroll]);

  return {
    containerRef,
    scrollToBottom,
    resetScroll,
    isScrolledUp: () => userScrolledUp.current,
  };
}

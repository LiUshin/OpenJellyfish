import { Suspense, useState, useCallback, useRef, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Button, Tooltip, Drawer, Badge, Dropdown } from 'antd';
import {
  GearSix, List as ListIcon, X, UserCircle, SignOut,
  ChatCircleDots, Stack, Timer, Cpu,
} from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../stores/authContext';
import { useFileWorkspace } from '../stores/fileWorkspaceContext';
import { useIsMobile, useMediaQuery } from '../hooks/useMediaQuery';
import FilePanel from '../components/FilePanel';
import FilePreview from '../components/FilePreview';
import ApiKeyWarning from '../components/ApiKeyWarning';
import WorkspaceSidebarHeader from '../components/WorkspaceSidebarHeader';
import * as api from '../services/api';
import RoutePending from '../components/RoutePending';
import workspace from './workspace.module.css';
import { setLanguage, currentLang, type SupportedLang } from '../i18n';

const { Sider, Content } = Layout;

export default function AppLayout() {
  const { user, logout } = useAuth();
  const {
    editingFile,
    splitMode,
    splitRatio,
    setSplitRatio,
    closeFile,
    fileBrowserOpen,
    setFileBrowserOpen,
  } = useFileWorkspace();
  const navigate = useNavigate();
  const location = useLocation();
  const isMobile = useIsMobile();
  const isCompact = useMediaQuery('(max-width: 1199px)');
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const [navDrawerOpen, setNavDrawerOpen] = useState(false);
  const [sidebarSlot, setSidebarSlot] = useState<HTMLDivElement | null>(null);
  const [workspaceSplitRatio, setWorkspaceSplitRatio] = useState(0.65);
  const [inboxUnread, setInboxUnread] = useState(0);
  const path = location.pathname;
  const inRoute = (base: string) => path === base || path.startsWith(`${base}/`);
  const isChat = path === '/';
  const isServiceManagement = inRoute('/settings/services');
  const isService = isServiceManagement || inRoute('/settings/inbox');
  const isScheduler = inRoute('/settings/scheduler');
  const isEnvironment = inRoute('/settings/environment') || inRoute('/settings/packages');
  const isSettings = inRoute('/settings') && !isService && !isScheduler && !isEnvironment;
  const hideContextSidebar = isServiceManagement || isScheduler;
  const environmentLabel = currentLang() === 'en' ? 'Environment' : '环境';
  const username = user?.username || t('login.username');
  const primaryNav = [
    { key: 'chat', label: t('nav.chat'), to: '/', icon: <ChatCircleDots size={22} />, active: isChat },
    { key: 'service', label: currentLang() === 'en' ? 'Services' : '服务', to: '/settings/services', icon: <Stack size={22} />, active: isService },
    { key: 'scheduler', label: t('nav.scheduler'), to: '/settings/scheduler', icon: <Timer size={22} />, active: isScheduler },
    { key: 'environment', label: environmentLabel, to: '/settings/environment', icon: <Cpu size={22} />, active: isEnvironment },
  ];

  // Reconcile UI language with the user's stored preference once after sign-in.
  // The local UI may already be set (localStorage / navigator); if backend
  // disagrees we adopt the backend value (more authoritative across devices).
  // First-ever login (empty backend pref) pushes the local pick up so other
  // devices see it on next sign-in.
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    (async () => {
      try {
        const prefs = await api.getPreferences();
        if (cancelled) return;
        const stored = (prefs.language || '').trim() as SupportedLang | '';
        if (stored && stored !== currentLang()) {
          await setLanguage(stored);
        } else if (!stored) {
          try { await api.updatePreferences({ language: currentLang() }); } catch { /* best-effort */ }
        }
      } catch {
        // Ignore — we already have a working language from localStorage.
      }
    })();
    return () => { cancelled = true; };
  }, [user]);

  useEffect(() => {
    if (!user) return;
    let stopped = false;
    let busy = false;
    const refresh = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const result = await api.getInboxUnreadCount();
        if (!stopped) setInboxUnread(result.count);
      } catch { /* Keep the previous count during a transient outage. */ }
      finally { busy = false; }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    window.addEventListener('focus', refresh);
    window.addEventListener('inbox-changed', refresh);
    return () => {
      stopped = true;
      clearInterval(timer);
      window.removeEventListener('focus', refresh);
      window.removeEventListener('inbox-changed', refresh);
    };
  }, [user]);

  // Preserve the chat split mode. Other sections always retain their main page.
  const showPreview = !!editingFile && (!isChat || splitMode !== 'chat');
  const showMain = !isChat || splitMode !== 'file' || !editingFile;
  const activeSplitRatio = isChat ? splitRatio : workspaceSplitRatio;
  const previewAsDrawer = isCompact || isMobile;
  const drawerPreviewOpen = previewAsDrawer && showPreview;

  // Auto-close the nav drawer when switching route on mobile (tap menu item).
  useEffect(() => {
    if (isMobile) setNavDrawerOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  /* ──────────────────────────────────────────────────────────────
     移动端 FilePanel ↔ FilePreview 互切：
       - 点开文件（editingFile null → path）且 FilePanel 打开时 → 关闭 FilePanel，
         记住这是"从面板打开"的，以便后续自动回到面板；
       - 关闭预览（editingFile path → null）且上一步有记录 → 重新打开 FilePanel。
     桌面端该 effect 是 no-op（两个组件并排显示，不必互切）。
     ────────────────────────────────────────────────────────────── */
  const prevEditingRef = useRef<string | null>(editingFile);
  const reopenPanelRef = useRef<boolean>(false);
  useEffect(() => {
    const prev = prevEditingRef.current;
    prevEditingRef.current = editingFile;
    if (!isMobile) {
      reopenPanelRef.current = false;
      return;
    }
    if (!prev && editingFile) {
      if (fileBrowserOpen) {
        reopenPanelRef.current = true;
        setFileBrowserOpen(false);
      }
      return;
    }
    if (prev && !editingFile) {
      if (reopenPanelRef.current) {
        reopenPanelRef.current = false;
        setFileBrowserOpen(true);
      }
    }
  }, [editingFile, isMobile, fileBrowserOpen, setFileBrowserOpen]);

  const dividerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const setActiveSplitRatio = useCallback((ratio: number) => {
    if (isChat) setSplitRatio(ratio);
    else setWorkspaceSplitRatio(Math.max(0.55, Math.min(0.8, ratio)));
  }, [isChat, setSplitRatio]);

  const dividerCleanup = useRef<(() => void) | null>(null);
  useEffect(() => () => { dividerCleanup.current?.(); }, []);
  const onDividerDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dividerCleanup.current?.();
    const container = contentRef.current;
    if (!container) return;

    let frame = 0;
    let latestRatio: number | null = null;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMove = (ev: globalThis.MouseEvent) => {
      const rect = container.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const ratio = x / rect.width;
      latestRatio = ratio;
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; if (latestRatio !== null) setActiveSplitRatio(latestRatio); });
    };
    const onUp = () => {
      cancelAnimationFrame(frame);
      if (latestRatio !== null) setActiveSplitRatio(latestRatio);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      dividerCleanup.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    dividerCleanup.current = onUp;
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [setActiveSplitRatio]);

  const renderAccountControl = () => (
    <Dropdown
      trigger={['click']}
      placement="topLeft"
      menu={{ items: [
        { key: 'username', label: username, disabled: true },
        { type: 'divider' },
        {
          key: 'logout',
          icon: <SignOut size={16} />,
          label: t('common.logout'),
          onClick: () => { setNavDrawerOpen(false); logout(); },
        },
      ] }}
    >
      <button
        type="button"
        aria-label={username}
        aria-haspopup="menu"
        style={{ width: 42, height: 42, display: 'grid', placeItems: 'center', border: 0, borderRadius: 9, cursor: 'pointer', background: 'transparent', color: 'var(--jf-text-muted)' }}
      >
        <UserCircle size={22} />
      </button>
    </Dropdown>
  );

  // Common sidebar inner content — reused by both desktop Sider and mobile Drawer.
  // Kept DOM-identical so #sider-slot portal target works in both modes.
  const renderSidebarContents = (isCollapsed: boolean) => (
    <>
      <WorkspaceSidebarHeader collapsed={isCollapsed}
        onToggleCollapse={isMobile ? undefined : () => setCollapsed(!collapsed)} />

      <div id="sider-slot" ref={setSidebarSlot} style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', visibility: isCollapsed || hideContextSidebar ? 'hidden' : undefined }} />

      {isMobile && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderTop: '1px solid var(--jf-border)', flexShrink: 0 }}>
          <Tooltip title={t('nav.settings')} placement="top">
            <button
              type="button" aria-label={t('nav.settings')}
              aria-current={isSettings ? 'page' : undefined}
              onClick={() => { setNavDrawerOpen(false); navigate('/settings/general'); }}
              style={{ width: 42, height: 42, display: 'grid', placeItems: 'center', border: 0, borderRadius: 9, cursor: 'pointer', background: isSettings ? 'var(--jf-bg-raised)' : 'transparent', color: isSettings ? 'var(--jf-primary)' : 'var(--jf-text-muted)' }}
            >
              <GearSix size={22} />
            </button>
          </Tooltip>
          {renderAccountControl()}
        </div>
      )}
    </>
  );

  return (
    <Layout style={{ height: '100dvh', background: 'var(--jf-bg-deep)' }}>
      <ApiKeyWarning />

      {!isMobile && (
        <nav
          aria-label={currentLang() === 'en' ? 'Main navigation' : '全局功能'}
          style={{
            width: 56, minWidth: 56, height: '100%', display: 'flex',
            flexDirection: 'column', alignItems: 'center', gap: 8,
            padding: '12px 6px', boxSizing: 'border-box',
            background: 'var(--jf-bg-panel)', borderRight: '1px solid var(--jf-border)',
          }}
        >
          <img src="/media_resources/jellyfishlogo.png" alt="OpenJellyfish" width={34} height={34} style={{ objectFit: 'contain', marginBottom: 12 }} />
          {primaryNav.map((item) => (
            <Tooltip key={item.key} title={item.label} placement="right">
              <button
                type="button" aria-label={item.label}
                aria-current={item.active ? 'page' : undefined}
                onClick={() => navigate(item.to)}
                style={{
                  width: 42, height: 42, display: 'grid', placeItems: 'center',
                  border: 0, borderRadius: 9, cursor: 'pointer',
                  background: item.active ? 'var(--jf-bg-raised)' : 'transparent',
                  color: item.active ? 'var(--jf-primary)' : 'var(--jf-text-muted)',
                }}
              >
                {item.key === 'service' && inboxUnread > 0
                  ? <Badge count={inboxUnread} size="small" offset={[5, 1]}>{item.icon}</Badge>
                  : item.icon}
              </button>
            </Tooltip>
          ))}
          <div style={{ flex: 1 }} />
          <Tooltip title={t('nav.settings')} placement="right">
            <button
              type="button" aria-label={t('nav.settings')}
              aria-current={isSettings ? 'page' : undefined}
              onClick={() => navigate('/settings/general')}
              style={{
                width: 42, height: 42, display: 'grid', placeItems: 'center',
                border: 0, borderRadius: 9, cursor: 'pointer',
                background: isSettings ? 'var(--jf-bg-raised)' : 'transparent',
                color: isSettings ? 'var(--jf-primary)' : 'var(--jf-text-muted)',
              }}
            >
              <GearSix size={22} />
            </button>
          </Tooltip>
          {renderAccountControl()}
        </nav>
      )}

      {isMobile ? (
        // ── Mobile: Sider rendered as a Drawer; #sider-slot portal target
        // stays alive via forceRender so Chat's createPortal survives close.
        <Drawer
          placement="left"
          open={navDrawerOpen}
          onClose={() => setNavDrawerOpen(false)}
          forceRender
          width="min(85vw, 320px)"
          closeIcon={null}
          styles={{
            body: {
              padding: 0,
              background: 'var(--jf-bg-panel)',
              display: 'flex',
              flexDirection: 'column',
              height: '100%',
            },
            header: { display: 'none' },
            wrapper: { background: 'var(--jf-bg-panel)' },
          }}
          rootStyle={{ zIndex: 1050 }}
        >
          <nav
            aria-label={currentLang() === 'en' ? 'Main navigation' : '全局功能'}
            style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6, padding: '12px 12px 8px', borderBottom: '1px solid var(--jf-border)', flexShrink: 0 }}
          >
            {primaryNav.map((item) => (
              <button
                key={item.key} type="button" aria-label={item.label}
                aria-current={item.active ? 'page' : undefined}
                onClick={() => { setNavDrawerOpen(false); navigate(item.to); }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, minWidth: 0,
                  minHeight: 40, padding: '7px 9px', border: 0, borderRadius: 8,
                  background: item.active ? 'var(--jf-bg-raised)' : 'transparent',
                  color: item.active ? 'var(--jf-primary)' : 'var(--jf-text)',
                  cursor: 'pointer', fontSize: 13, textAlign: 'left',
                }}
              >
                {item.icon}<span>{item.label}</span>
                {item.key === 'service' && inboxUnread > 0 && <Badge count={inboxUnread} size="small" style={{ marginLeft: 'auto' }} />}
              </button>
            ))}
          </nav>
          {renderSidebarContents(false)}
        </Drawer>
      ) : (
        <Sider
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          width={240}
          collapsedWidth={64}
          theme="dark"
          style={{
            display: hideContextSidebar ? 'none' : 'flex',
            background: 'var(--jf-bg-panel)',
            borderRight: '1px solid var(--jf-border)',
            transition: 'width 0.18s ease-out',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
          trigger={null}
        >
          {renderSidebarContents(collapsed)}
        </Sider>
      )}

      <Content
        style={{
          background: 'var(--jf-bg-deep)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'row',
          flex: 1,
          position: 'relative',
        }}
      >
        {/* Mobile-only: floating hamburger button at top-left to open nav Drawer.
            top/left 使用 safe-area-inset 偏移，避免刘海/圆角遮住。 */}
        {isMobile && !navDrawerOpen && (
          <Button
            type="text"
            icon={<ListIcon size={22} weight="bold" />}
            onClick={() => setNavDrawerOpen(true)}
            aria-label={t('header.openMenu')}
            style={{
              position: 'absolute',
              top: 'calc(6px + env(safe-area-inset-top, 0px))',
              left: 'calc(6px + env(safe-area-inset-left, 0px))',
              zIndex: 20,
              width: 36,
              height: 36,
              padding: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--jf-text)',
              background: 'transparent',
              borderRadius: 'var(--jf-radius-md)',
            }}
          />
        )}

        {/* Main content area: current route + shared file preview */}
        <div
          ref={contentRef}
          style={{
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'row',
            overflow: 'hidden',
          }}
        >
          {/* Keep the route mounted whenever a non-chat section opens a file. */}
          <div style={{
            flex: showMain ? (showPreview && !previewAsDrawer ? activeSplitRatio : 1) : 0,
            minWidth: 0,
            minHeight: 0,
            display: (showMain || isMobile) ? 'flex' : 'none',
            flexDirection: 'column',
            overflow: 'hidden',
          }}>
            <Suspense fallback={<RoutePending />}><Outlet context={{ sidebarSlot, closeNavigation: () => setNavDrawerOpen(false), inboxUnread }} /></Suspense>
          </div>

          {/* Resizable divider — wide desktop only; compact previews use a Drawer. */}
          {!previewAsDrawer && showMain && showPreview && (
            <div
              ref={dividerRef}
              className={workspace.divider}
              role="separator" aria-orientation="vertical" tabIndex={0}
              aria-label={t('header.resizePanels')} title={t('header.resizePanelsHint')}
              aria-valuemin={isChat ? 15 : 55} aria-valuemax={isChat ? 85 : 80} aria-valuenow={Math.round(activeSplitRatio * 100)}
              onDoubleClick={() => setActiveSplitRatio(isChat ? 0.5 : 0.65)}
              onKeyDown={event => {
                const next = event.key === 'ArrowLeft' ? activeSplitRatio - 0.025 : event.key === 'ArrowRight' ? activeSplitRatio + 0.025
                  : event.key === 'Home' ? (isChat ? 0.15 : 0.55) : event.key === 'End' ? (isChat ? 0.85 : 0.8) : event.key === 'Enter' ? (isChat ? 0.5 : 0.65) : null;
                if (next !== null) { event.preventDefault(); setActiveSplitRatio(next); }
              }}
              onMouseDown={onDividerDown}
              style={{
                width: 5,
                flexShrink: 0,
                cursor: 'col-resize',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'background 0.15s',
              }}
            >
              <div style={{
                width: 3,
                height: 32,
                borderRadius: 2,
                background: 'rgba(var(--jf-text-rgb, 200,200,200), 0.2)',
              }} />
            </div>
          )}

          {/* File preview is inline on wide desktop. */}
          {!previewAsDrawer && showPreview && (
            <div style={{
              flex: showMain ? (1 - activeSplitRatio) : 1,
              minWidth: 0,
              minHeight: 0,
              overflow: 'hidden',
              borderLeft: showMain ? undefined : `1px solid var(--jf-border)`,
            }}>
              <FilePreview />
            </div>
          )}
        </div>

        {/* Compact FilePreview Drawer over the current route.
            onClose 触发时（ESC / 遮罩点击 / swipe）同步清空 editingFile —— 否则
            抽屉关了但状态仍认为文件在编辑，下一次 openFile 打开时会看到旧文件闪
            一下。FilePreview 自身的 X 按钮仍能关闭（调同一个 closeFile）。 */}
        {previewAsDrawer && (
          <Drawer
            placement="right"
            open={drawerPreviewOpen}
            onClose={() => closeFile()}
            width={isMobile ? '100vw' : 'min(82vw, 680px)'}
            closeIcon={<X size={20} />}
            title={null}
            styles={{
              body: { padding: 0, background: 'var(--jf-bg-deep)' },
              header: { display: 'none' },
              wrapper: { background: 'var(--jf-bg-deep)' },
            }}
            rootStyle={{ zIndex: 1040 }}
            destroyOnClose={false}
          >
            <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <FilePreview />
            </div>
          </Drawer>
        )}

        {/* On compact desktop the browser overlays the page instead of
            shrinking its content; on mobile FilePanel uses its own Drawer. */}
        <div style={isCompact && !isMobile
          ? { position: 'absolute', top: 0, right: 0, bottom: 0, display: 'flex', zIndex: 30 }
          : { display: 'contents' }}>
          <FilePanel />
        </div>
      </Content>
    </Layout>
  );
}

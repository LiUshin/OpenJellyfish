import { useState, useCallback, useRef, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Button, Avatar, Tooltip, Drawer } from 'antd';
import {
  SignOut, GearSix, ArrowLeft, Sun, Moon, List as ListIcon, X,
} from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../stores/authContext';
import { useTheme } from '../stores/themeContext';
import { useFileWorkspace } from '../stores/fileWorkspaceContext';
import { useIsMobile } from '../hooks/useMediaQuery';
import FilePanel from '../components/FilePanel';
import FilePreview from '../components/FilePreview';
import ApiKeyWarning from '../components/ApiKeyWarning';
import LanguageSwitcher from '../components/LanguageSwitcher';
import * as api from '../services/api';
import { setLanguage, currentLang, type SupportedLang } from '../i18n';

const { Sider, Content } = Layout;

export default function AppLayout() {
  const { user, logout } = useAuth();
  const { isDark, toggleColor } = useTheme();
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
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const [navDrawerOpen, setNavDrawerOpen] = useState(false);
  const isSettings = location.pathname.startsWith('/settings');

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

  // On mobile the FilePreview is a full-screen Drawer, not an inline split.
  const showPreview = !isSettings && !!editingFile && splitMode !== 'chat';
  const showChat = isSettings || splitMode !== 'file' || !editingFile;
  const mobilePreviewOpen = isMobile && showPreview;

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

  const onDividerDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const container = contentRef.current;
    if (!container) return;

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMove = (ev: globalThis.MouseEvent) => {
      const rect = container.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const ratio = x / rect.width;
      setSplitRatio(ratio);
    };
    const onUp = () => {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [setSplitRatio]);

  // Common sidebar inner content — reused by both desktop Sider and mobile Drawer.
  // Kept DOM-identical so #sider-slot portal target works in both modes.
  const renderSidebarContents = (isCollapsed: boolean) => (
    <>
      {/* Top: user row */}
      <div
        style={{
          flexShrink: 0,
          padding: '7px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          height: 47,
          boxSizing: 'border-box',
        }}
      >
        <Avatar
          size={32}
          style={{ background: 'var(--jf-legacy)', fontWeight: 700, flexShrink: 0 }}
        >
          {user?.username?.charAt(0).toUpperCase() || 'U'}
        </Avatar>
        {!isCollapsed && (
          <span
            style={{
              color: 'var(--jf-text)',
              fontWeight: 500,
              fontSize: 13,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              flex: 1,
              minWidth: 0,
            }}
          >
            {user?.username || t('login.username')}
          </span>
        )}
        {/* Language switcher sits to the left of the Settings/Back button so
            it's always visible from the chat sidebar (per UX requirement). */}
        <LanguageSwitcher variant="icon" placement="bottom" />
        {isSettings ? (
          <Tooltip title={t('common.back')} placement="right">
            <Button
              type="text"
              icon={<ArrowLeft size={20} />}
              style={{ color: 'var(--jf-text-muted)', flexShrink: 0 }}
              onClick={() => navigate('/')}
            />
          </Tooltip>
        ) : (
          <Tooltip title={t('settings.title')} placement="right">
            <Button
              type="text"
              icon={<GearSix size={20} />}
              style={{ color: 'var(--jf-text-muted)', flexShrink: 0 }}
              onClick={() => navigate('/settings')}
            />
          </Tooltip>
        )}
      </div>

      <div id="sider-slot" style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }} />

      {/* Bottom: Brand + dark/light toggle */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '16px 20px 12px',
          flexShrink: 0,
        }}
      >
        <img
          src="/media_resources/jellyfishlogo.png"
          alt=""
          width={32}
          height={32}
          style={{ flexShrink: 0, objectFit: 'contain', display: 'block', cursor: isMobile ? 'default' : 'pointer' }}
          onClick={() => { if (!isMobile) setCollapsed(!collapsed); }}
        />
        {!isCollapsed && (
          <span
            style={{
              color: 'var(--jf-text)',
              fontWeight: 600,
              fontSize: 15,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              flex: 1,
              cursor: isMobile ? 'default' : 'pointer',
            }}
            onClick={() => { if (!isMobile) setCollapsed(!collapsed); }}
          >
            OpenJellyfish
          </span>
        )}
        <Tooltip title={isDark ? t('header.switchToLight') : t('header.switchToDark')} placement="top">
          <Button
            type="text"
            size="small"
            icon={isDark ? <Sun size={16} /> : <Moon size={16} />}
            style={{
              color: 'var(--jf-text-muted)',
              flexShrink: 0,
              width: 28,
              height: 28,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            onClick={(e) => { e.stopPropagation(); toggleColor(); }}
          />
        </Tooltip>
      </div>
      <div style={{ flexShrink: 0, padding: '0 16px 12px' }}>
        <Tooltip title={t('common.logout')} placement="right">
          <Button
            type="text"
            icon={<SignOut size={18} />}
            style={{ color: 'var(--jf-text-muted)', width: '100%', justifyContent: isCollapsed ? 'center' : 'flex-start' }}
            onClick={logout}
          >
            {!isCollapsed && t('common.logout')}
          </Button>
        </Tooltip>
      </div>
    </>
  );

  return (
    <Layout style={{ height: '100vh', background: 'var(--jf-bg-deep)' }}>
      <ApiKeyWarning />

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
            background: 'var(--jf-bg-panel)',
            borderRight: '1px solid var(--jf-border)',
            transition: 'width 0.3s ease-in-out',
            display: 'flex',
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

        {/* Main content area: chat + file preview */}
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
          {/* Chat/Settings area — always mounted so the sidebar portal stays alive */}
          <div style={{
            flex: isSettings ? 1 : (showChat ? (showPreview && !isMobile ? splitRatio : 1) : 0),
            minWidth: 0,
            minHeight: 0,
            display: (isSettings || showChat || isMobile) ? 'flex' : 'none',
            flexDirection: 'column',
            overflow: 'hidden',
          }}>
            <Outlet />
          </div>

          {/* Resizable divider — desktop only, hidden on mobile (preview becomes Drawer) */}
          {!isMobile && showChat && showPreview && (
            <div
              ref={dividerRef}
              onMouseDown={onDividerDown}
              style={{
                width: 5,
                flexShrink: 0,
                cursor: 'col-resize',
                background: 'var(--jf-border)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'background 0.15s',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'var(--jf-primary)'; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'var(--jf-border)'; }}
            >
              <div style={{
                width: 3,
                height: 32,
                borderRadius: 2,
                background: 'rgba(var(--jf-text-rgb, 200,200,200), 0.2)',
              }} />
            </div>
          )}

          {/* File preview area — inline on desktop, fullscreen Drawer on mobile */}
          {!isMobile && showPreview && (
            <div style={{
              flex: showChat ? (1 - splitRatio) : 1,
              minWidth: 0,
              minHeight: 0,
              overflow: 'hidden',
              borderLeft: showChat ? undefined : `1px solid var(--jf-border)`,
            }}>
              <FilePreview />
            </div>
          )}
        </div>

        {/* Mobile-only: FilePreview as fullscreen Drawer over chat.
            onClose 触发时（ESC / 遮罩点击 / swipe）同步清空 editingFile —— 否则
            抽屉关了但状态仍认为文件在编辑，下一次 openFile 打开时会看到旧文件闪
            一下。FilePreview 自身的 X 按钮仍能关闭（调同一个 closeFile）。 */}
        {isMobile && (
          <Drawer
            placement="right"
            open={mobilePreviewOpen}
            onClose={() => closeFile()}
            width="100vw"
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

        {/* File browser panel — hidden in settings but stays mounted. FilePanel decides
            itself whether to render inline (desktop) or as a Drawer (mobile). */}
        <div style={{ display: isSettings ? 'none' : 'contents' }}>
          <FilePanel />
        </div>
      </Content>
    </Layout>
  );
}

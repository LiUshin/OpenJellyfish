import { useState, useCallback, useRef } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Button, Avatar, Tooltip } from 'antd';
import { SignOut, GearSix, ArrowLeft, Sun, Moon } from '@phosphor-icons/react';
import { useAuth } from '../stores/authContext';
import { useTheme } from '../stores/themeContext';
import { useFileWorkspace } from '../stores/fileWorkspaceContext';
import FilePanel from '../components/FilePanel';
import FilePreview from '../components/FilePreview';
import ApiKeyWarning from '../components/ApiKeyWarning';

const { Sider, Content } = Layout;

export default function AppLayout() {
  const { user, logout } = useAuth();
  const { isDark, toggleColor } = useTheme();
  const {
    editingFile,
    splitMode,
    splitRatio,
    setSplitRatio,
  } = useFileWorkspace();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const isSettings = location.pathname.startsWith('/settings');

  const showPreview = !isSettings && !!editingFile && splitMode !== 'chat';
  const showChat = isSettings || splitMode !== 'file' || !editingFile;

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

  return (
    <Layout style={{ height: '100vh', background: 'var(--jf-bg-deep)' }}>
      <ApiKeyWarning />
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
          {!collapsed && (
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
              {user?.username || '用户'}
            </span>
          )}
          {isSettings ? (
            <Tooltip title="返回对话" placement="right">
              <Button
                type="text"
                icon={<ArrowLeft size={20} />}
                style={{ color: 'var(--jf-text-muted)', flexShrink: 0 }}
                onClick={() => navigate('/')}
              />
            </Tooltip>
          ) : (
            <Tooltip title="设置" placement="right">
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
            style={{ flexShrink: 0, objectFit: 'contain', display: 'block', cursor: 'pointer' }}
            onClick={() => setCollapsed(!collapsed)}
          />
          {!collapsed && (
            <span
              style={{
                color: 'var(--jf-text)',
                fontWeight: 600,
                fontSize: 15,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                flex: 1,
                cursor: 'pointer',
              }}
              onClick={() => setCollapsed(!collapsed)}
            >
              JellyfishBot
            </span>
          )}
          <Tooltip title={isDark ? '切换浅色' : '切换深色'} placement="top">
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
          <Tooltip title="退出登录" placement="right">
            <Button
              type="text"
              icon={<SignOut size={18} />}
              style={{ color: 'var(--jf-text-muted)', width: '100%', justifyContent: collapsed ? 'center' : 'flex-start' }}
              onClick={logout}
            >
              {!collapsed && '退出登录'}
            </Button>
          </Tooltip>
        </div>
      </Sider>

      <Content
        style={{
          background: 'var(--jf-bg-deep)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'row',
          flex: 1,
        }}
      >
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
            flex: isSettings ? 1 : (showChat ? (showPreview ? splitRatio : 1) : 0),
            minWidth: 0,
            minHeight: 0,
            display: (isSettings || showChat) ? 'flex' : 'none',
            flexDirection: 'column',
            overflow: 'hidden',
          }}>
            <Outlet />
          </div>

          {/* Resizable divider */}
          {showChat && showPreview && (
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

          {/* File preview area */}
          {showPreview && (
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

        {/* File browser panel (right side) — hidden in settings but stays mounted */}
        <div style={{ display: isSettings ? 'none' : 'contents' }}>
          <FilePanel />
        </div>
      </Content>
    </Layout>
  );
}

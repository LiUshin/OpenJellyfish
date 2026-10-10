import '../../components/SettingsWorkspace.css';
import { useMemo, Suspense } from 'react';
import { Outlet, useNavigate, useLocation, useOutletContext } from 'react-router-dom';
import { createPortal } from 'react-dom';
import { Badge, Menu } from 'antd';
import type { MenuProps } from 'antd';
import {
  NotePencil,
  UsersThree,
  Stack,
  ChatTeardropDots,
  Tray,
  Package,
  GearSix,
  Archive,
  ChartBar,
  BookOpen,
} from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import './settings.css';
import RoutePending from '../../components/RoutePending';
import HeaderControls from '../../components/HeaderControls';

export default function SettingsLayout() {
  const navigate = useNavigate();
  const { closeNavigation, sidebarSlot: siderSlot, inboxUnread } = useOutletContext<{ closeNavigation: () => void; sidebarSlot: HTMLElement | null; inboxUnread: number }>();
  const location = useLocation();
  const { t, i18n } = useTranslation();
  const path = location.pathname;
  const isServices = path.startsWith('/settings/services');
  const isScheduler = path.startsWith('/settings/scheduler');
  const isInbox = path.startsWith('/settings/inbox');
  const isEnvironment = path.startsWith('/settings/environment') || path.startsWith('/settings/packages');
  const isChinese = i18n.language.toLowerCase().startsWith('zh');
  const serviceNav = useMemo<MenuProps['items']>(() => [
    { key: '/settings/services', icon: <Stack size={18} />, label: isChinese ? '我的服务' : 'My services' },
    { key: '/settings/inbox', icon: <Tray size={18} />, label: (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        {t('settingsPolish.pages.inbox.title')}
        {inboxUnread > 0 && <Badge count={inboxUnread} size="small" style={{ backgroundColor: 'var(--jf-error)' }} />}
      </span>
    ) },
  ], [inboxUnread, isChinese, t]);
  const environmentNav = useMemo<MenuProps['items']>(() => [
    { key: '/settings/environment', icon: <GearSix size={18} />, label: isChinese ? '模型与连接' : 'Models & connections' },
    { key: '/settings/packages', icon: <Package size={18} />, label: isChinese ? 'Python 依赖' : 'Python packages' },
  ], [isChinese]);
  const settingsNav = useMemo<MenuProps['items']>(() => [
    { key: '/settings/general', icon: <GearSix size={18} />, label: t('settingsPolish.pages.general.title') },
    { type: 'group', label: t('ux.agentGroup'), children: [
      { key: '/settings/prompt', icon: <NotePencil size={18} />, label: t('settingsPolish.pages.prompt.title') },
      { key: '/settings/subagents', icon: <UsersThree size={18} />, label: t('settingsPolish.pages.subagents.title') },
    ] },
    { type: 'group', label: t('ux.workspaceGroup'), children: [
      { key: '/settings/wechat', icon: <ChatTeardropDots size={18} />, label: t('settingsPolish.pages.wechat.title') },
      { key: '/settings/usage', icon: <ChartBar size={18} />, label: t('settingsPolish.pages.usage.title') },
      { key: '/settings/backup', icon: <Archive size={18} />, label: t('settingsPolish.pages.backup.title') },
    ] },
    { key: 'tutorials', icon: <BookOpen size={18} />, label: (
      <a href="https://openjellyfish.ai/zh/tutorials/" target="_blank" rel="noopener noreferrer">
        {isChinese ? '教程' : 'Tutorials'} ↗
      </a>
    ) },
  ], [t, isChinese]);
  const navItems = isInbox ? serviceNav : isServices || isScheduler ? null : isEnvironment ? environmentNav : settingsNav;
  const pageId = path.split('/').pop() || 'prompt';
  const pageName = t(`settingsPolish.pages.${pageId}.title`);
  const splitPage = pageId === 'services' || pageId === 'scheduler' || pageId === 'prompt';

  const areaName = isServices || isInbox ? (isChinese ? '服务' : 'Services')
    : isScheduler ? t('settingsPolish.pages.scheduler.title')
      : isEnvironment ? (isChinese ? '环境' : 'Environment') : t('settings.title');
  const sidebarContent = navItems && (
    <nav aria-label={areaName} className="jf-settings-nav">
      <div className="jf-settings-label">{areaName}</div>
      <Menu
        mode="inline"
        selectedKeys={[path]}
        onClick={({ key }) => { if (key !== 'tutorials') navigate(key); closeNavigation(); }}
        style={{ background: 'transparent', borderRight: 'none', fontSize: 13 }}
        items={navItems}
      />
    </nav>
  );

  return (
    <>
      {siderSlot && sidebarContent && createPortal(sidebarContent, siderSlot)}
      <div className="settings-shell">
        {!isServices && !isScheduler && (
          <header className="settings-heading">
            <p>{areaName}</p>
            <h1>{pageName}</h1>
            <div className="settings-description">{t(`settingsPolish.pages.${pageId}.description`)}</div>
            <div className="settings-file-controls"><HeaderControls /></div>
          </header>
        )}
        <div className={`settings-content ${splitPage ? 'settings-content-split' : ''}`} data-page={pageId}>
          <Suspense fallback={<RoutePending />}><Outlet /></Suspense>
        </div>
      </div>
    </>
  );
}

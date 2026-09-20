import '../../components/SettingsWorkspace.css';
import { useState, useEffect, useMemo, Suspense } from 'react';
import { Outlet, useNavigate, useLocation, useOutletContext } from 'react-router-dom';
import { createPortal } from 'react-dom';
import { Badge, Menu } from 'antd';
import {
  NotePencil,
  UsersThree,
  Stack,
  Timer,
  ChatTeardropDots,
  Tray,
  Package,
  GearSix,
  Archive,
  Microphone,
  ChartBar,
} from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import './settings.css';
import RoutePending from '../../components/RoutePending';
import * as api from '../../services/api';

export default function SettingsLayout() {
  const navigate = useNavigate();
  const { closeNavigation, sidebarSlot: siderSlot } = useOutletContext<{ closeNavigation: () => void; sidebarSlot: HTMLElement | null }>();
  const location = useLocation();
  const { t } = useTranslation();
  const [inboxUnread, setInboxUnread] = useState(0);


  useEffect(() => {
    api.getInboxUnreadCount().then((r) => setInboxUnread(r.count)).catch(() => {});
  }, [location.pathname]);

  const settingsNav = useMemo(() => [
    { key: '/settings/prompt', icon: <NotePencil size={18} />, label: t('settingsPolish.pages.prompt.title') },
    { key: '/settings/subagents', icon: <UsersThree size={18} />, label: t('settingsPolish.pages.subagents.title') },
    { key: '/settings/packages', icon: <Package size={18} />, label: t('settingsPolish.pages.packages.title') },
    { key: '/settings/services', icon: <Stack size={18} />, label: t('settingsPolish.pages.services.title') },
    { key: '/settings/scheduler', icon: <Timer size={18} />, label: t('settingsPolish.pages.scheduler.title') },
    { key: '/settings/wechat', icon: <ChatTeardropDots size={18} />, label: t('settingsPolish.pages.wechat.title') },
    { key: '/settings/voice', icon: <Microphone size={18} />, label: t('settingsPolish.pages.voice.title') },
    {
      key: '/settings/inbox',
      icon: <Tray size={18} />,
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {t('settingsPolish.pages.inbox.title')}
          {inboxUnread > 0 && (
            <Badge count={inboxUnread} size="small" style={{ backgroundColor: 'var(--jf-error)' }} />
          )}
        </span>
      ),
    },
    { key: '/settings/usage', icon: <ChartBar size={18} />, label: t('settingsPolish.pages.usage.title') },
    { key: '/settings/general', icon: <GearSix size={18} />, label: t('settingsPolish.pages.general.title') },
    { key: '/settings/backup', icon: <Archive size={18} />, label: t('settingsPolish.pages.backup.title') },
  ], [inboxUnread, t]);

  const selectedKey = settingsNav.find((item) =>
    location.pathname.startsWith(item.key),
  )?.key ?? settingsNav[0].key;

  const pageId = selectedKey.split('/').pop() || 'prompt';
  const pageName = t(`settingsPolish.pages.${pageId}.title`);
  const splitPage = pageId === 'services' || pageId === 'scheduler' || pageId === 'prompt';

  const sidebarContent = (
    <nav aria-label={t('settings.title')} className="jf-settings-nav">
      <div className="jf-settings-label">{t('settings.title')}</div>
      <Menu
        mode="inline"
        selectedKeys={[selectedKey]}
        onClick={({ key }) => { navigate(key); closeNavigation(); }}
        style={{ background: 'transparent', borderRight: 'none', fontSize: 13 }}
        items={[
          { type: 'group', label: t('ux.agentGroup'), children: settingsNav.slice(0, 3) },
          { type: 'group', label: t('ux.deliverGroup'), children: settingsNav.slice(3, 8) },
          { type: 'group', label: t('ux.workspaceGroup'), children: settingsNav.slice(8) },
        ]}
      />
    </nav>
  );

  return (
    <>
      {siderSlot && createPortal(sidebarContent, siderSlot)}
      <div className="settings-shell">
        <header className={`settings-heading ${pageId === 'services' || pageId === 'scheduler' ? 'settings-heading-wide' : ''}`}>
          <p>{t('settings.title')}</p>
          <h1>{pageName}</h1>
          <div>{t(`settingsPolish.pages.${pageId}.description`)}</div>
        </header>
        <div className={`settings-content ${splitPage ? 'settings-content-split' : ''}`} data-page={pageId}>
          <Suspense fallback={<RoutePending />}><Outlet /></Suspense>
        </div>
      </div>
    </>
  );
}

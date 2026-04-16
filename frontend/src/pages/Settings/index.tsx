import { useState, useEffect, useMemo } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
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
} from '@phosphor-icons/react';
import * as api from '../../services/api';

export default function SettingsLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [siderSlot, setSiderSlot] = useState<HTMLElement | null>(null);
  const [inboxUnread, setInboxUnread] = useState(0);

  useEffect(() => {
    const el = document.getElementById('sider-slot');
    setSiderSlot(el);
  }, []);

  useEffect(() => {
    api.getInboxUnreadCount().then((r) => setInboxUnread(r.count)).catch(() => {});
  }, [location.pathname]);

  const settingsNav = useMemo(() => [
    { key: '/settings/prompt', icon: <NotePencil size={18} />, label: 'Prompt' },
    { key: '/settings/subagents', icon: <UsersThree size={18} />, label: 'Subagent 管理' },
    { key: '/settings/packages', icon: <Package size={18} />, label: 'Python 环境' },
    { key: '/settings/services', icon: <Stack size={18} />, label: 'Service 管理' },
    { key: '/settings/scheduler', icon: <Timer size={18} />, label: '定时任务' },
    { key: '/settings/wechat', icon: <ChatTeardropDots size={18} />, label: '微信接入' },
    {
      key: '/settings/inbox',
      icon: <Tray size={18} />,
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          收件箱
          {inboxUnread > 0 && (
            <Badge count={inboxUnread} size="small" style={{ backgroundColor: 'var(--jf-error)' }} />
          )}
        </span>
      ),
    },
    { key: '/settings/general', icon: <GearSix size={18} />, label: '通用' },
  ], [inboxUnread]);

  const selectedKey = settingsNav.find((item) =>
    location.pathname.startsWith(item.key),
  )?.key ?? settingsNav[0].key;

  const sidebarContent = (
    <div style={{ flex: 1, overflow: 'auto', paddingTop: 4 }}>
      <Menu
        mode="inline"
        selectedKeys={[selectedKey]}
        onClick={({ key }) => navigate(key)}
        style={{ background: 'transparent', borderRight: 'none', fontSize: 13 }}
        items={settingsNav}
      />
    </div>
  );

  return (
    <>
      {siderSlot && createPortal(sidebarContent, siderSlot)}
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', display: 'flex', flexDirection: 'column', background: 'var(--jf-bg-deep)' }}>
        <Outlet />
      </div>
    </>
  );
}

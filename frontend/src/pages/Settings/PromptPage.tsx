import { useState, useEffect, useMemo } from 'react';
import { Tabs, Tag } from 'antd';
import UserProfileEditor from '../../components/modals/UserProfileEditor';
import SystemPromptEditor from '../../components/modals/SystemPromptEditor';
import SoulSettings from '../../components/modals/SoulSettings';

const ADV_SYSTEM_KEY = 'show_advanced_system';
const ADV_SOUL_KEY = 'show_advanced_soul';

export default function PromptPage() {
  const [tab, setTab] = useState<'profile' | 'system' | 'soul'>('profile');
  const [showSystem, setShowSystem] = useState(localStorage.getItem(ADV_SYSTEM_KEY) === '1');
  const [showSoul, setShowSoul] = useState(localStorage.getItem(ADV_SOUL_KEY) === '1');

  useEffect(() => {
    const handler = () => {
      setShowSystem(localStorage.getItem(ADV_SYSTEM_KEY) === '1');
      setShowSoul(localStorage.getItem(ADV_SOUL_KEY) === '1');
    };
    window.addEventListener('advanced-settings-changed', handler);
    return () => window.removeEventListener('advanced-settings-changed', handler);
  }, []);

  useEffect(() => {
    if (tab === 'system' && !showSystem) setTab('profile');
    if (tab === 'soul' && !showSoul) setTab('profile');
  }, [showSystem, showSoul, tab]);

  const tabItems = useMemo(() => {
    const items: { key: string; label: React.ReactNode }[] = [
      { key: 'profile', label: '个性规则' },
    ];
    if (showSystem) {
      items.push({
        key: 'system',
        label: (
          <span>
            操作规则
            <Tag color="purple" style={{ marginLeft: 6, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
              Advanced
            </Tag>
          </span>
        ),
      });
    }
    if (showSoul) {
      items.push({
        key: 'soul',
        label: (
          <span>
            Memory & Soul
            <Tag color="purple" style={{ marginLeft: 6, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
              Advanced
            </Tag>
          </span>
        ),
      });
    }
    return items;
  }, [showSystem, showSoul]);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '12px 24px 0', flexShrink: 0 }}>
        <Tabs
          activeKey={tab}
          onChange={(k) => setTab(k as 'profile' | 'system' | 'soul')}
          items={tabItems}
          style={{ marginBottom: 0 }}
        />
      </div>
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <UserProfileEditor open={tab === 'profile'} onClose={() => {}} inline />
        {showSystem && <SystemPromptEditor open={tab === 'system'} onClose={() => {}} inline />}
        {showSoul && <SoulSettings open={tab === 'soul'} onClose={() => {}} inline />}
      </div>
    </div>
  );
}

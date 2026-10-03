import { useState, useEffect, useMemo } from 'react';
import { Tabs, Tag } from 'antd';
import { useTranslation } from 'react-i18next';
import UserProfileEditor from '../../components/modals/UserProfileEditor';
import SystemPromptEditor from '../../components/modals/SystemPromptEditor';
import SoulSettings from '../../components/modals/SoulSettings';

const ADV_SYSTEM_KEY = 'show_advanced_system';
const ADV_SOUL_KEY = 'show_advanced_soul';

export default function PromptPage() {
  const { t } = useTranslation();
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
      { key: 'profile', label: t('promptPage.tabRules') },
    ];
    if (showSystem) {
      items.push({
        key: 'system',
        label: (
          <span className="prompt-tab-label">
            <span>{t('promptPage.tabOpsRules')}</span>
            <Tag color="purple" className="prompt-tab-advanced">
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
          <span className="prompt-tab-label">
            <span>{t('promptPage.tabMemorySoul')}</span>
            <Tag color="purple" className="prompt-tab-advanced">
              Advanced
            </Tag>
          </span>
        ),
      });
    }
    return items;
  }, [showSystem, showSoul, t]);

  return (
    <div className="prompt-page">
      {tabItems.length > 1 && <div className="prompt-page-tabs">
        <Tabs
          activeKey={tab}
          onChange={(k) => setTab(k as 'profile' | 'system' | 'soul')}
          items={tabItems}
          style={{ marginBottom: 0 }}
        />
      </div>}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <UserProfileEditor open={tab === 'profile'} onClose={() => {}} inline />
        {showSystem && <SystemPromptEditor open={tab === 'system'} onClose={() => {}} inline />}
        {showSoul && <SoulSettings open={tab === 'soul'} onClose={() => {}} inline />}
      </div>
    </div>
  );
}

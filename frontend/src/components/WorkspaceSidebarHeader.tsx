import { Button, Tooltip } from 'antd';
import { CaretDoubleRight, Moon, Sun } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import { useTheme } from '../stores/themeContext';
import LanguageSwitcher from './LanguageSwitcher';
import workspace from '../layouts/workspace.module.css';

export default function WorkspaceSidebarHeader({ collapsed = false, onToggleCollapse }: {
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}) {
  const { isDark, toggleColor } = useTheme();
  const { t } = useTranslation();
  const brand = collapsed ? <CaretDoubleRight size={18} /> : <span>OpenJellyfish</span>;

  return (
    <div style={{
      flexShrink: 0,
      padding: collapsed ? '7px 4px' : '7px 10px 7px 16px',
      flexDirection: collapsed ? 'column' : 'row',
      display: 'flex', alignItems: 'center',
      gap: collapsed ? 2 : 4,
      minHeight: collapsed ? 82 : 52,
      boxSizing: 'border-box',
    }}>
      {onToggleCollapse ? (
        <button type="button" className={workspace.brandButton}
          style={{ justifyContent: collapsed ? 'center' : 'flex-start' }}
          aria-label={t(collapsed ? 'header.expandSidebar' : 'header.collapseSidebar')}
          title={t(collapsed ? 'header.expandSidebar' : 'header.collapseSidebar')}
          onClick={onToggleCollapse}>
          {brand}
        </button>
      ) : (
        <div className={workspace.brandButton} style={{ cursor: 'default', justifyContent: 'flex-start' }}>{brand}</div>
      )}
      <Tooltip title={isDark ? t('header.switchToLight') : t('header.switchToDark')} placement="bottom">
        <Button
          type="text" size="small"
          aria-label={isDark ? t('header.switchToLight') : t('header.switchToDark')}
          icon={isDark ? <Sun size={16} /> : <Moon size={16} />}
          style={{ color: 'var(--jf-text-muted)', flexShrink: 0, width: 28, height: 28,
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 0 }}
          onClick={toggleColor}
        />
      </Tooltip>
      {!collapsed && <LanguageSwitcher variant="icon" placement="bottom" />}
    </div>
  );
}

import { useCallback } from 'react';
import { Dropdown, Button, Tooltip, message } from 'antd';
import { Translate } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import { setLanguage, currentLang, type SupportedLang } from '../i18n';
import * as api from '../services/api';

type Variant = 'icon' | 'compact';

interface Props {
  /** ``icon`` shows a globe button (sidebar/header use); ``compact`` shows a labeled button (settings use). */
  variant?: Variant;
  /** Tooltip placement for the icon variant. */
  placement?: 'top' | 'right' | 'bottom' | 'left';
  /** Sync the choice to backend preferences too (default true). Set false on Login screen etc. */
  syncBackend?: boolean;
  className?: string;
}

const LANG_LABELS: Record<SupportedLang, string> = {
  zh: '中文',
  en: 'English',
};

/**
 * Language picker. Single source of truth is i18next; backend preference is
 * eventually-consistent (best-effort PUT). Failure to sync only swallows a
 * console error — the local switch still happens because Accept-Language is
 * derived from localStorage on the next request.
 */
export default function LanguageSwitcher({
  variant = 'icon',
  placement = 'top',
  syncBackend = true,
  className,
}: Props) {
  const { t, i18n } = useTranslation();
  const lang = currentLang();

  const onPick = useCallback(async (next: SupportedLang) => {
    if (next === lang) return;
    await setLanguage(next);
    if (syncBackend) {
      try {
        await api.updatePreferences({ language: next });
      } catch (e) {
        console.warn('[LanguageSwitcher] failed to sync language preference', e);
      }
    }
    message.success(t('general.languageSaved'));
  }, [lang, syncBackend, t]);

  const items = (Object.keys(LANG_LABELS) as SupportedLang[]).map((k) => ({
    key: k,
    label: LANG_LABELS[k],
    onClick: () => { void onPick(k); },
  }));

  if (variant === 'compact') {
    return (
      <Dropdown menu={{ items, selectedKeys: [lang] }} trigger={['click']} placement="bottomRight">
        <Button className={className} icon={<Translate size={16} />}>
          {LANG_LABELS[lang]}
        </Button>
      </Dropdown>
    );
  }

  // icon variant — fits naturally next to other 28×28 icon buttons.
  return (
    <Tooltip title={t('header.language')} placement={placement} key={i18n.language}>
      <Dropdown menu={{ items, selectedKeys: [lang] }} trigger={['click']} placement="bottomRight">
        <Button
          type="text"
          icon={<Translate size={20} />}
          className={className}
          style={{ color: 'var(--jf-text-muted)', flexShrink: 0 }}
          aria-label="Language"
        />
      </Dropdown>
    </Tooltip>
  );
}

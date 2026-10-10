import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

/** Host management has no admin preferences session; reuse only the local locale. */
export function useRuntimeCopy() {
  const { i18n } = useTranslation();
  const english = i18n.language.toLowerCase().startsWith('en');
  const text = useCallback((zh: string, en: string) => english ? en : zh, [english]);
  return { text, english };
}

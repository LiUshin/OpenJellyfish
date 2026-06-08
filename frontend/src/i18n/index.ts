/**
 * i18n initialization — react-i18next + browser language detector.
 *
 * Strategy:
 * - One JSON dict per locale (zh.json / en.json) keyed by `namespace.key`;
 *   no react-i18next "namespace" feature for now (single bundle, easier to grep).
 * - Persisted in localStorage (`jf-lang`); first visit derives from
 *   `navigator.language`, falling back to zh when neither zh* nor en*.
 * - Backend preferences (`/api/preferences.language`) is the authoritative
 *   cross-device source — `useLanguageSync()` reconciles on app mount.
 * - antd's `ConfigProvider locale` is wired in App.tsx by reading
 *   `i18n.language`, so date pickers / pagination / etc. follow.
 *
 * To add a new key: add it to BOTH zh.json and en.json (same key).
 * Missing keys fall back to the key string itself (i18next default), which
 * makes accidental untranslated text visible during dev.
 */

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import zh from './locales/zh.json';
import en from './locales/en.json';

export const SUPPORTED_LANGS = ['zh', 'en'] as const;
export type SupportedLang = (typeof SUPPORTED_LANGS)[number];

export const LANG_LS_KEY = 'jf-lang';

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
    },
    fallbackLng: 'zh',
    supportedLngs: [...SUPPORTED_LANGS],
    nonExplicitSupportedLngs: true,
    load: 'languageOnly',
    detection: {
      order: ['localStorage', 'navigator', 'htmlTag'],
      lookupLocalStorage: LANG_LS_KEY,
      caches: ['localStorage'],
    },
    interpolation: { escapeValue: false },
    returnNull: false,
    react: { useSuspense: false },
  });

export function setLanguage(lang: SupportedLang): Promise<unknown> {
  if (!SUPPORTED_LANGS.includes(lang)) return Promise.resolve();
  try { localStorage.setItem(LANG_LS_KEY, lang); } catch { /* private mode */ }
  document.documentElement.setAttribute('lang', lang);
  return i18n.changeLanguage(lang);
}

/** Normalised current language (always one of SUPPORTED_LANGS). */
export function currentLang(): SupportedLang {
  const raw = (i18n.language || 'zh').toLowerCase();
  if (raw.startsWith('en')) return 'en';
  return 'zh';
}

export default i18n;

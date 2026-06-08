import { useEffect, useState } from 'react';
import { ConfigProvider, App as AntdApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import type { Locale } from 'antd/es/locale';
import i18n from 'i18next';
import { useTranslation } from 'react-i18next';
import { AuthProvider } from './stores/authContext';
import { StreamProvider } from './stores/streamContext';
import { ThemeProvider, useTheme } from './stores/themeContext';
import { FileWorkspaceProvider } from './stores/fileWorkspaceContext';
import AppRouter from './router';

/** Map i18n language → antd Locale bundle. Falls back to zhCN. */
function pickAntdLocale(lang: string | undefined): Locale {
  if ((lang || '').toLowerCase().startsWith('en')) return enUS;
  return zhCN;
}

function ThemedApp() {
  const { antdConfig } = useTheme();
  // Re-render whenever i18n language flips so antd ConfigProvider's locale follows.
  const { i18n: i18nInst } = useTranslation();
  const [antdLocale, setAntdLocale] = useState<Locale>(() => pickAntdLocale(i18n.language));

  useEffect(() => {
    const handler = (lng: string) => {
      setAntdLocale(pickAntdLocale(lng));
      document.documentElement.setAttribute('lang', lng.toLowerCase().startsWith('en') ? 'en' : 'zh');
    };
    handler(i18nInst.language);
    i18nInst.on('languageChanged', handler);
    return () => { i18nInst.off('languageChanged', handler); };
  }, [i18nInst]);

  return (
    <ConfigProvider theme={antdConfig} locale={antdLocale}>
      <AntdApp>
        <AuthProvider>
          <StreamProvider>
            <FileWorkspaceProvider>
              <AppRouter />
            </FileWorkspaceProvider>
          </StreamProvider>
        </AuthProvider>
      </AntdApp>
    </ConfigProvider>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <ThemedApp />
    </ThemeProvider>
  );
}

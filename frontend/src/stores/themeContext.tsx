import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { ThemeConfig } from 'antd';
import { theme as antdTheme } from 'antd';

const COLOR_KEY = 'jf-color';
const STYLE_KEY = 'jf-style';

export type ColorMode = 'dark' | 'light';
export type UiStyle = 'regular' | 'terminal';

interface ThemeContextValue {
  colorMode: ColorMode;
  uiStyle: UiStyle;
  setColorMode: (c: ColorMode) => void;
  setUiStyle: (s: UiStyle) => void;
  toggleColor: () => void;
  isDark: boolean;
  antdConfig: ThemeConfig;
  /** @deprecated — use colorMode + uiStyle instead */
  themeName: string;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readColor(): ColorMode {
  try {
    const v = localStorage.getItem(COLOR_KEY);
    if (v === 'dark' || v === 'light') return v;
    const legacy = localStorage.getItem('jf-theme');
    if (legacy === 'cyber-ocean') return 'light';
    return 'dark';
  } catch { return 'dark'; }
}

function readStyle(): UiStyle {
  try {
    const v = localStorage.getItem(STYLE_KEY);
    if (v === 'regular' || v === 'terminal') return v;
    const legacy = localStorage.getItem('jf-theme');
    if (legacy === 'terminal') return 'terminal';
    return 'regular';
  } catch { return 'regular'; }
}

function applyToDOM(color: ColorMode, style: UiStyle) {
  const el = document.documentElement;
  el.setAttribute('data-color', color);
  el.setAttribute('data-style', style);
  el.removeAttribute('data-theme');
}

function buildAntdConfig(color: ColorMode, style: UiStyle): ThemeConfig {
  const isDark = color === 'dark';
  const isTerminal = style === 'terminal';

  const PALETTES: Record<string, Record<string, string>> = {
    dark: {
      colorPrimary: '#E89FD9',
      colorBgBase: '#0f0f13',
      colorBgContainer: '#16161d',
      colorBgElevated: '#1c1c27',
      colorBgLayout: '#0f0f13',
      colorBorder: '#2a2a3a',
      colorBorderSecondary: '#33334a',
      colorText: '#e4e4ed',
      colorTextSecondary: '#9494a8',
      colorTextTertiary: '#66668a',
      colorTextQuaternary: '#44445a',
      colorSuccess: '#3ecf8e',
      colorError: '#FF6B9D',
      colorWarning: '#FFB86C',
      colorInfo: '#8B7FD9',
    },
    light: {
      colorPrimary: '#18269e',
      colorBgBase: '#f0f4f8',
      colorBgContainer: '#ffffff',
      colorBgElevated: '#e8edf4',
      colorBgLayout: '#f0f4f8',
      colorBorder: '#d0dae6',
      colorBorderSecondary: '#b8c8d8',
      colorText: '#1a2a44',
      colorTextSecondary: '#5a6a80',
      colorTextTertiary: '#8898aa',
      colorTextQuaternary: '#aab8c8',
      colorSuccess: '#0ea56f',
      colorError: '#d63060',
      colorWarning: '#d4850a',
      colorInfo: '#4d8de8',
    },
  };

  const MENU_COLORS: Record<string, Record<string, string>> = {
    dark: {
      darkItemBg: 'transparent',
      darkItemSelectedBg: '#2a2a3a',
      darkItemHoverBg: '#22222f',
      darkItemColor: '#9494a8',
      darkItemSelectedColor: '#e4e4ed',
    },
    light: {
      darkItemBg: 'transparent',
      darkItemSelectedBg: 'rgba(24, 38, 158, 0.08)',
      darkItemHoverBg: 'rgba(24, 38, 158, 0.04)',
      darkItemColor: '#5a6a80',
      darkItemSelectedColor: '#18269e',
    },
  };

  const palette = PALETTES[color];
  const menuColors = MENU_COLORS[color];

  const LAYOUT_BG: Record<string, { siderBg: string; bodyBg: string }> = {
    dark: { siderBg: '#16161d', bodyBg: '#0f0f13' },
    light: { siderBg: '#ffffff', bodyBg: '#f0f4f8' },
  };

  const HOVER_BORDER: Record<string, string> = {
    dark: '#8B7FD9',
    light: '#4d8de8',
  };

  const OPTION_BG: Record<string, string> = {
    dark: '#2a2a3a',
    light: 'rgba(24, 38, 158, 0.06)',
  };

  return {
    token: {
      ...palette,
      borderRadius: isTerminal ? 0 : 8,
      fontFamily: isTerminal
        ? "'JetBrains Mono', 'Fira Code', Consolas, monospace"
        : "'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif",
      fontSize: 14,
      controlHeight: 36,
    },
    components: {
      Menu: menuColors,
      Layout: LAYOUT_BG[color],
      Button: { primaryShadow: 'none', defaultShadow: 'none' },
      Input: {
        activeBorderColor: palette.colorPrimary,
        hoverBorderColor: HOVER_BORDER[color],
      },
      Select: {
        optionSelectedBg: OPTION_BG[color],
      },
      Tabs: {
        inkBarColor: palette.colorPrimary,
        itemSelectedColor: palette.colorText,
        itemColor: palette.colorTextSecondary,
      },
    },
    algorithm: isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
  };
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [colorMode, setColorState] = useState<ColorMode>(readColor);
  const [uiStyle, setStyleState] = useState<UiStyle>(readStyle);

  useEffect(() => {
    applyToDOM(colorMode, uiStyle);
  }, [colorMode, uiStyle]);

  const setColorMode = useCallback((c: ColorMode) => {
    setColorState(c);
    try { localStorage.setItem(COLOR_KEY, c); } catch { /* noop */ }
  }, []);

  const setUiStyle = useCallback((s: UiStyle) => {
    setStyleState(s);
    try { localStorage.setItem(STYLE_KEY, s); } catch { /* noop */ }
  }, []);

  const toggleColor = useCallback(() => {
    setColorMode(colorMode === 'dark' ? 'light' : 'dark');
  }, [colorMode, setColorMode]);

  const isDark = colorMode === 'dark';
  const antdConfig = useMemo(() => buildAntdConfig(colorMode, uiStyle), [colorMode, uiStyle]);

  const themeName = uiStyle === 'terminal'
    ? `terminal-${colorMode}`
    : colorMode;

  const value = useMemo<ThemeContextValue>(
    () => ({ colorMode, uiStyle, setColorMode, setUiStyle, toggleColor, isDark, antdConfig, themeName }),
    [colorMode, uiStyle, setColorMode, setUiStyle, toggleColor, isDark, antdConfig, themeName],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}

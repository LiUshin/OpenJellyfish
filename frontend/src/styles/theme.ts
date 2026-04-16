/**
 * Design-token constants for use in JS/TS when CSS vars aren't practical.
 * The canonical source of truth is themes.css (CSS variables).
 * These constants represent the DARK theme defaults and are kept for
 * backward compatibility; prefer var(--jf-*) in styles.
 */

export const brandColors = {
  primary: '#E89FD9',
  secondary: '#8B7FD9',
  accent: '#5FC9E6',
  highlight: '#FF8FCC',
  legacy: '#6c5ce7',
};

export const semanticColors = {
  success: '#5FC9E6',
  warning: '#FFB86C',
  error: '#FF6B9D',
  info: '#8B7FD9',
};

export const bgColors = {
  base: '#0f0f13',
  container: '#16161d',
  elevated: '#1c1c27',
};

export const textColors = {
  primary: '#e4e4ed',
  secondary: '#9494a8',
  tertiary: '#66668a',
  quaternary: '#44445a',
};

export const borderColors = {
  primary: '#2a2a3a',
  secondary: '#33334a',
};

export const radius = {
  sm: 4,
  md: 8,
  lg: 12,
  bubble: 16,
};

export const shadows = {
  float: '0 2px 8px rgba(0,0,0,0.3)',
  hover: '0 4px 16px rgba(0,0,0,0.4)',
  brand: '0 8px 24px rgba(232,159,217,0.2)',
};

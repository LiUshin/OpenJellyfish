import './SettingsWorkspace.css';
import type { ReactNode } from 'react';

/** Page-local sections remain mounted so switching never discards a draft. */
export default function SettingsSectionNav({ label, value, onChange, items }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  items: { value: string; label: string; description?: string; icon?: ReactNode }[];
}) {
  return (
    <nav className="settings-sections" aria-label={label}>
      {items.map(item => (
        <button key={item.value} type="button" aria-pressed={value === item.value}
          onClick={() => onChange(item.value)}>
          {item.icon && <span className="settings-section-icon" aria-hidden="true">{item.icon}</span>}
          <span><strong>{item.label}</strong>{item.description && <small>{item.description}</small>}</span>
        </button>
      ))}
    </nav>
  );
}

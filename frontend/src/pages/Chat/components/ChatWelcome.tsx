import { ArrowUpRight, FileText, Sparkle, Stack, Timer } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import styles from '../chat.module.css';

const SUGGESTION_KEYS = [
  { icon: <FileText size={20} />, key: 'document' },
  { icon: <Sparkle size={20} />, key: 'research' },
  { icon: <Timer size={20} />, key: 'routine' },
  { icon: <Stack size={20} />, key: 'service' },
];

export default function ChatWelcome({ onSuggest, onConfigure }: {
  onSuggest: (prompt: string) => void;
  onConfigure: () => void;
}) {
  const { t } = useTranslation();

  return <section className={styles.emptyState} aria-labelledby="welcome-heading">
    <div className={styles.welcomeBrand}>
      <img src="/media_resources/jellyfishlogo.png" alt="" width="64" height="64" />
      <span>YOUR JELLYFISH WORKSPACE</span>
    </div>
    <h1 id="welcome-heading" className={styles.welcomeHeading}>{t('ux.welcomeTitle')}</h1>
    <p className={styles.welcomeDescription}>{t('ux.welcomeDescription')}</p>
    <div className={styles.starterGrid}>
      {SUGGESTION_KEYS.map(s => <button type="button" key={s.key} className={styles.starterCard}
        onClick={() => onSuggest(t(`ux.starters.${s.key}.prompt`))}>
        <span className={styles.starterIcon}>{s.icon}</span>
        <ArrowUpRight size={16} className={styles.starterArrow} />
        <strong>{t(`ux.starters.${s.key}.title`)}</strong>
        <span>{t(`ux.starters.${s.key}.description`)}</span>
      </button>)}
    </div>
    <div className={styles.welcomeFootnote}>
      <span>{t('ux.starterHint')}</span>
      <button type="button" onClick={onConfigure}>{t('ux.configureEngine')} <ArrowUpRight size={13} /></button>
    </div>
  </section>;
}

import { useTranslation } from 'react-i18next';
import HeaderControls from './HeaderControls';

export default function SplitWorkspaceHeading({ page }: { page: 'services' | 'scheduler' }) {
  const { t } = useTranslation();

  return (
    <header className="settings-split-heading">
      <div className="settings-split-heading-copy">
        <h1>{t(`settingsPolish.pages.${page}.title`)}</h1>
        <p>{t(`settingsPolish.pages.${page}.description`)}</p>
      </div>
      <HeaderControls />
    </header>
  );
}

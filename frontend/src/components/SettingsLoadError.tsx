import { Alert, Button } from 'antd';
import { useTranslation } from 'react-i18next';

export default function SettingsLoadError({ onRetry, detail }: { onRetry: () => void; detail?: string }) {
  const { t } = useTranslation();
  return <Alert type="error" showIcon message={t('settingsPolish.loadFailed')} description={detail || t('settingsPolish.loadFailedHint')}
    action={<Button onClick={onRetry}>{t('ux.retry')}</Button>} />;
}

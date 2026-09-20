import { Spin } from 'antd';
import { useTranslation } from 'react-i18next';
export default function RoutePending() {
  const { t } = useTranslation();
  return <div role="status" className="jf-route-pending"><Spin /><span>{t('ux.loading')}</span></div>;
}

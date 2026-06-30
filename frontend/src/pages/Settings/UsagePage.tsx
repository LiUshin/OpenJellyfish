import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Spin, Empty, Segmented, message } from 'antd';
import { ChartLineUp } from '@phosphor-icons/react';
import { getUsageSummary, type UsageSummary } from '../../services/api';
import UsageView from '../../components/UsageView';
import styles from './UsagePage.module.css';

export default function UsagePage() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [months, setMonths] = useState(3);
  const [data, setData] = useState<UsageSummary | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getUsageSummary(months));
    } catch (e) {
      message.error(String(e));
    } finally {
      setLoading(false);
    }
  }, [months]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className={styles.page}>
      <div className={styles.aurora}>
        <b />
        <b />
        <b />
      </div>

      <div className={styles.inner}>
        <div className={styles.header}>
          <div className={styles.icon}>
            <ChartLineUp size={24} weight="bold" />
          </div>
          <div>
            <h2 className={styles.title}>{t('usage.title', '用量统计')}</h2>
            <div className={styles.subtitle}>{t('usage.selfSub', '我的 LLM 调用遥测 · 按模型 / Service / Key 拆解')}</div>
          </div>
          <div className={styles.spacer} />
          <Segmented
            value={months}
            onChange={(v) => setMonths(v as number)}
            options={[
              { label: t('usage.month1', '近 1 月'), value: 1 },
              { label: t('usage.month3', '近 3 月'), value: 3 },
              { label: t('usage.month6', '近 6 月'), value: 6 },
            ]}
          />
        </div>

        {loading && (
          <div className={styles.loadingWrap}>
            <Spin />
          </div>
        )}

        {!loading && !data && (
          <div className={styles.emptyWrap}>
            <Empty description={t('usage.empty', '暂无用量数据')} />
          </div>
        )}

        {!loading && data && <UsageView data={data} />}
      </div>
    </div>
  );
}

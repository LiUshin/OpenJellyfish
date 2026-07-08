import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Empty } from 'antd';
import { type UsageSummary, type UsageRow } from '../services/api';
import styles from '../pages/Settings/UsagePage.module.css';

/* ──────────────────────────────────────────────────────────────────────
 * 共享用量可视化（V3 玻璃霓虹）。
 * UsagePage（admin 全量）与 AdminServices（单 service）共用同一套渲染，
 * 通过 `dims` 控制显示哪些维度，避免重复渲染逻辑（DRY）。
 * 全部走 --jf-* 主题变量，自适应 dark/light × regular/terminal。
 * ──────────────────────────────────────────────────────────────────── */

const fmtNum = (n: number) => n.toLocaleString();
const fmtTok = (n: number) => {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
};
const pct = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0);

// 环形图配色（全部主题变量，dark/light/terminal 自适应）
const DONUT_COLORS = [
  'var(--jf-primary)',
  'var(--jf-accent)',
  'var(--jf-secondary)',
  'var(--jf-warning)',
  'var(--jf-success)',
  'var(--jf-info)',
];

/** 控制显示哪些维度。未传的字段默认 true（admin 全量）。 */
export interface UsageDims {
  channel?: boolean;
  model?: boolean;
  day?: boolean;
  service?: boolean;
  provider?: boolean;
  key?: boolean;
}

const ALL_DIMS: Required<UsageDims> = {
  channel: true,
  model: true,
  day: true,
  service: true,
  provider: true,
  key: true,
};

export default function UsageView({ data, dims }: { data: UsageSummary; dims?: UsageDims }) {
  const { t } = useTranslation();
  const d = { ...ALL_DIMS, ...(dims || {}) };
  const { total } = data;

  const channelCard = (
    <div className={styles.glass}>
      <CardTitle title={t('usage.byChannel', '按渠道')} badge={`${data.by_channel.length} CH`} />
      <Donut rows={data.by_channel} totalTok={total.total_tokens} />
    </div>
  );
  const modelCard = (
    <div className={styles.glass}>
      <CardTitle
        title={t('usage.byModel', '按模型')}
        badge={`TOP ${Math.min(data.by_model.length, 8)} / ${data.by_model.length}`}
      />
      <BarList rows={data.by_model} limit={8} variant="default" />
    </div>
  );
  const providerCard = (
    <div className={styles.glass}>
      <CardTitle title={t('usage.byProvider', '按 Provider')} badge={`${data.by_provider.length}`} />
      <BarList rows={data.by_provider} limit={8} variant="default" />
    </div>
  );
  const keyCard = (
    <div className={styles.glass}>
      <CardTitle title={t('usage.byKey', '按 API Key')} badge={`${data.by_key.length}`} />
      <BarList rows={data.by_key} limit={8} variant="alt" />
    </div>
  );

  return (
    <>
      {/* 汇总 */}
      <div className={styles.statGrid}>
        <StatCard
          color="cy"
          label={t('usage.inputTokens', '输入 Tokens')}
          value={fmtTok(total.input_tokens)}
          sub={t('usage.share', '占比') + ' ' + pct(total.input_tokens, total.total_tokens) + '%'}
        />
        <StatCard
          color="pk"
          label={t('usage.outputTokens', '输出 Tokens')}
          value={fmtTok(total.output_tokens)}
          sub={t('usage.share', '占比') + ' ' + pct(total.output_tokens, total.total_tokens) + '%'}
        />
        <StatCard
          color="gr"
          label={t('usage.totalTokens', '总 Tokens')}
          value={fmtTok(total.total_tokens)}
          sub={'≈ ' + fmtTok(total.calls > 0 ? Math.round(total.total_tokens / total.calls) : 0) + ' / ' + t('usage.perCall', '次')}
        />
        <StatCard
          color="pu"
          label={t('usage.calls', '调用次数')}
          value={fmtNum(total.calls)}
          sub={(data.months_scanned || 0) + ' ' + t('usage.monthsScanned', '个月数据')}
        />
      </div>

      {/* 渠道环形 + 模型条 */}
      {d.channel && d.model ? (
        <div className={styles.mainGrid}>
          {channelCard}
          {modelCard}
        </div>
      ) : (
        <>
          {d.channel && <div style={{ marginBottom: 16 }}>{channelCard}</div>}
          {d.model && <div style={{ marginBottom: 16 }}>{modelCard}</div>}
        </>
      )}

      {/* 每日趋势 */}
      {d.day && (
        <div className={styles.glass} style={{ marginBottom: 16 }}>
          <CardTitle title={t('usage.trend', '每日趋势')} badge={`${data.by_day.length} ${t('usage.days', '天')}`} />
          <AreaChart rows={data.by_day} />
          <div className={styles.legend}>
            <span>
              <i style={{ background: 'var(--jf-accent)' }} />
              {t('usage.inputTokens', '输入 Tokens')}
            </span>
            <span>
              <i style={{ background: 'var(--jf-primary)' }} />
              {t('usage.outputTokens', '输出 Tokens')}
            </span>
          </div>
        </div>
      )}

      {/* Service 使用状况（高亮） */}
      {d.service && (
        <div className={styles.glass} style={{ marginBottom: 16 }}>
          <CardTitle title={t('usage.byService', '按 Service')} badge={`${data.by_service.length} SVC`} />
          <BarList rows={data.by_service} limit={12} variant="svc" />
        </div>
      )}

      {/* Provider + Key */}
      {d.provider && d.key ? (
        <div className={styles.twoCol}>
          {providerCard}
          {keyCard}
        </div>
      ) : (
        <>
          {d.provider && <div style={{ marginBottom: 16 }}>{providerCard}</div>}
          {d.key && <div style={{ marginBottom: 16 }}>{keyCard}</div>}
        </>
      )}

      <div className={styles.footer}>
        openjellyfish · {t('usage.footer', '本人用量 · 服务端聚合 llm_usage/*.jsonl')}
      </div>
    </>
  );
}

function StatCard({ color, label, value, sub }: { color: string; label: string; value: string; sub?: string }) {
  // value 可能形如 "12.4M"：拆出尾部单位做小号
  const m = /^([\d.,]+)([A-Za-z]*)$/.exec(value);
  return (
    <div className={`${styles.glass} ${styles.stat}`}>
      <div className={styles.ring} />
      <div className={styles.statLabel}>{label}</div>
      <div className={`${styles.statVal} ${styles[color]}`}>
        {m ? (
          <>
            {m[1]}
            {m[2] && <small>{m[2]}</small>}
          </>
        ) : (
          value
        )}
      </div>
      {sub && <div className={styles.statSub}>{sub}</div>}
    </div>
  );
}

function CardTitle({ title, badge }: { title: string; badge?: string }) {
  return (
    <div className={styles.cardTitle}>
      <span className={styles.dot} />
      <h3>{title}</h3>
      {badge && <span className={styles.cardBadge}>{badge}</span>}
    </div>
  );
}

function BarList({ rows, limit, variant }: { rows: UsageRow[]; limit: number; variant: 'default' | 'alt' | 'svc' }) {
  const { t } = useTranslation();
  const sorted = useMemo(() => [...rows].sort((a, b) => b.total_tokens - a.total_tokens).slice(0, limit), [rows, limit]);
  const max = Math.max(1, ...sorted.map((r) => r.total_tokens));
  const fillCls = variant === 'alt' ? styles.fillAlt : variant === 'svc' ? styles.fillSvc : styles.fill;

  if (sorted.length === 0) {
    return (
      <div style={{ padding: '6px 20px 18px' }}>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('usage.empty', '暂无用量数据')} />
      </div>
    );
  }

  return (
    <div className={styles.blist}>
      {sorted.map((r, i) => (
        <div className={styles.brow} key={r.id || r.name || i}>
          <div className={styles.bname} title={r.name}>
            <span>{String(i + 1).padStart(2, '0')}</span>
            {r.name || '—'}
          </div>
          <div className={styles.track}>
            <div className={fillCls} style={{ width: `${(r.total_tokens / max) * 100}%` }} />
          </div>
          <div className={styles.bval}>
            {fmtTok(r.total_tokens)}
            <div className={styles.bsub}>{fmtNum(r.calls)} {t('usage.callsShort', '次')}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Donut({ rows, totalTok }: { rows: UsageRow[]; totalTok: number }) {
  const { t } = useTranslation();
  const slices = useMemo(() => {
    const sorted = [...rows].sort((a, b) => b.total_tokens - a.total_tokens);
    const top = sorted.slice(0, 5);
    const restTok = sorted.slice(5).reduce((s, r) => s + r.total_tokens, 0);
    const list = top.map((r) => ({ name: r.name || '—', val: r.total_tokens }));
    if (restTok > 0) list.push({ name: t('usage.others', '其他'), val: restTok });
    const sum = Math.max(1, list.reduce((s, r) => s + r.val, 0));
    let acc = 0;
    return list.map((r, i) => {
      const p = (r.val / sum) * 100;
      const seg = {
        name: r.name,
        val: r.val,
        p,
        color: DONUT_COLORS[i % DONUT_COLORS.length],
        dasharray: `${p.toFixed(3)} ${(100 - p).toFixed(3)}`,
        dashoffset: (25 - acc).toFixed(3),
      };
      acc += p;
      return seg;
    });
  }, [rows, t]);

  if (slices.length === 0) {
    return (
      <div style={{ padding: '6px 20px 24px' }}>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('usage.empty', '暂无用量数据')} />
      </div>
    );
  }

  return (
    <div className={styles.donutWrap}>
      <div className={styles.donut}>
        <svg width="168" height="168" viewBox="0 0 36 36">
          <circle cx="18" cy="18" r="15.9155" fill="none" stroke="var(--jf-bg-inset)" strokeWidth="3.6" />
          {slices.map((s, i) => (
            <circle
              key={i}
              cx="18"
              cy="18"
              r="15.9155"
              fill="none"
              stroke={s.color}
              strokeWidth="3.6"
              strokeDasharray={s.dasharray}
              strokeDashoffset={s.dashoffset}
              strokeLinecap="butt"
            />
          ))}
        </svg>
        <div className={styles.donutCenter}>
          <div className={styles.donutBig}>{fmtTok(totalTok)}</div>
          <div className={styles.donutSm}>{t('usage.totalTokens', '总 Tokens')}</div>
        </div>
      </div>
      <div className={styles.dleg}>
        {slices.map((s, i) => (
          <div className={styles.dlegIt} key={i}>
            <i style={{ background: s.color }} />
            <span className={styles.dlegName} title={s.name}>
              {s.name}
            </span>
            <span className={styles.dlegPc}>
              {Math.round(s.p)}% · {fmtTok(s.val)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function AreaChart({ rows }: { rows: UsageRow[] }) {
  const { t } = useTranslation();
  const days = useMemo(() => [...rows].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)), [rows]);

  const W = 720;
  const H = 160;
  const padTop = 14;
  const padBottom = 10;

  const { inputLine, inputArea, outputLine, outputArea, dots, labels } = useMemo(() => {
    if (days.length === 0) return { inputLine: '', inputArea: '', outputLine: '', outputArea: '', dots: [] as { x: number; y: number }[], labels: [] as string[] };
    const maxV = Math.max(1, ...days.map((d) => Math.max(d.input_tokens, d.output_tokens)));
    const xAt = (i: number) => (days.length > 1 ? (i / (days.length - 1)) * W : W / 2);
    const yAt = (v: number) => padTop + (1 - v / maxV) * (H - padTop - padBottom);
    const ipts = days.map((d, i) => ({ x: xAt(i), y: yAt(d.input_tokens) }));
    const opts = days.map((d, i) => ({ x: xAt(i), y: yAt(d.output_tokens) }));
    const toLine = (pts: { x: number; y: number }[]) => pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
    const toArea = (pts: { x: number; y: number }[]) => `${toLine(pts)} L${pts[pts.length - 1].x.toFixed(1)},${H} L${pts[0].x.toFixed(1)},${H} Z`;
    // 标签：稀疏取首/中/尾，避免拥挤
    const lbls = days.map((d) => d.name.slice(5)); // 去掉年份前缀
    return { inputLine: toLine(ipts), inputArea: toArea(ipts), outputLine: toLine(opts), outputArea: toArea(opts), dots: ipts, labels: lbls };
  }, [days]);

  if (days.length === 0) {
    return (
      <div style={{ padding: '6px 20px 24px' }}>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('usage.empty', '暂无用量数据')} />
      </div>
    );
  }

  const labelStep = Math.max(1, Math.ceil(labels.length / 8));

  return (
    <div className={styles.chart}>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="usageGi" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(var(--jf-accent-rgb), 0.45)" />
            <stop offset="100%" stopColor="rgba(var(--jf-accent-rgb), 0)" />
          </linearGradient>
          <linearGradient id="usageGo" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(var(--jf-primary-rgb), 0.4)" />
            <stop offset="100%" stopColor="rgba(var(--jf-primary-rgb), 0)" />
          </linearGradient>
        </defs>
        <path d={inputArea} fill="url(#usageGi)" />
        <path d={inputLine} fill="none" stroke="var(--jf-accent)" strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
        <path d={outputArea} fill="url(#usageGo)" />
        <path d={outputLine} fill="none" stroke="var(--jf-primary)" strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
        {dots.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r="3" fill="var(--jf-accent)" />
        ))}
      </svg>
      <div className={styles.chartAxis}>
        {labels.map((l, i) => (i % labelStep === 0 || i === labels.length - 1 ? <span key={i}>{l}</span> : null)).filter(Boolean)}
      </div>
    </div>
  );
}

import type { CSSProperties } from 'react';

import styles from './RunIndicator.module.css';

/**
 * 会话运行状态指示器(自包含、无外部依赖)。
 *
 * - `running`:3×3 点阵涟漪,从中心按曼哈顿距离向外泛起(bloom ripple),表示进行中。
 * - `approval`:竖排 3 点、琥珀黄、中间深两头浅、静止,表示等待审批。
 *
 * 尊重 `prefers-reduced-motion`:减动偏好下涟漪停成静态淡显。
 */
export type RunIndicatorState = 'running' | 'approval';

// 3×3 网格中各点到中心的曼哈顿距离(0=中心,1=上下左右,2=四角),用作涟漪环序。
const RIPPLE_RINGS: readonly number[] = [2, 1, 2, 1, 0, 1, 2, 1, 2];

interface RunIndicatorProps {
  state: RunIndicatorState;
  /** 无障碍标签(通常由调用方传入本地化文案)。 */
  label?: string;
}

export function RunIndicator({ state, label }: RunIndicatorProps) {
  if (state === 'approval') {
    return (
      <span className={styles.approval} role="img" aria-label={label}>
        <i />
        <i />
        <i />
      </span>
    );
  }

  return (
    <span className={styles.running} role="img" aria-label={label}>
      {RIPPLE_RINGS.map((ring, i) => (
        <i key={i} style={{ '--dmx-ring': ring } as CSSProperties} />
      ))}
    </span>
  );
}

export default RunIndicator;

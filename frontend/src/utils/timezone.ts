const TZ_KEY = 'tz_offset_hours';
const DEFAULT_TZ = 8;

export function getTzOffset(): number {
  const raw = localStorage.getItem(TZ_KEY);
  if (raw !== null) {
    const n = parseFloat(raw);
    if (!isNaN(n) && n >= -12 && n <= 14) return n;
  }
  return DEFAULT_TZ;
}

export function setTzOffset(hours: number): void {
  localStorage.setItem(TZ_KEY, String(hours));
}

export function tzLabel(offset?: number): string {
  const h = offset ?? getTzOffset();
  const sign = h >= 0 ? '+' : '';
  return `UTC${sign}${Number.isInteger(h) ? h : h}`;
}

function applyOffset(date: Date, offsetHours: number): Date {
  const utcMs = date.getTime() + date.getTimezoneOffset() * 60_000;
  return new Date(utcMs + offsetHours * 3_600_000);
}

function pad(n: number): string {
  return n < 10 ? '0' + n : String(n);
}

export function fmtUserTime(
  iso: string | undefined | null,
  format: 'datetime' | 'date' | 'time' | 'short' | 'timeonly' = 'datetime',
): string {
  if (!iso) return '';
  try {
    const d = applyOffset(new Date(iso), getTzOffset());
    const Y = d.getFullYear();
    const M = pad(d.getMonth() + 1);
    const D = pad(d.getDate());
    const h = pad(d.getHours());
    const m = pad(d.getMinutes());
    const s = pad(d.getSeconds());

    switch (format) {
      case 'datetime':
        return `${Y}-${M}-${D} ${h}:${m}:${s}`;
      case 'date':
        return `${Y}-${M}-${D}`;
      case 'time':
        return `${h}:${m}:${s}`;
      case 'short':
        return `${M}/${D} ${h}:${m}`;
      case 'timeonly':
        return `${h}:${m}`;
      default:
        return `${Y}-${M}-${D} ${h}:${m}:${s}`;
    }
  } catch {
    return iso;
  }
}

export function userNow(): string {
  return fmtUserTime(new Date().toISOString(), 'datetime');
}

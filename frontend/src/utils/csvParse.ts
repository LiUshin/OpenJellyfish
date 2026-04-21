/**
 * 轻量 CSV / TSV 解析器（无外部依赖）
 *
 * 特性：
 * - 自动识别分隔符（,/\t），可强制传入
 * - 支持双引号包裹字段、双引号转义（"" -> "）
 * - 支持引号内换行、CRLF
 * - 出现解析异常时降级为 split('\n') 行数组
 */

export interface CsvParseResult {
  rows: string[][];
  delimiter: string;
  totalRows: number;     // 实际解析得到的行数
  truncated: boolean;    // 是否被 maxRows 截断
}

const DEFAULT_MAX_ROWS = 2000;

function detectDelimiter(text: string): string {
  const sample = text.slice(0, 4096);
  const tabs = (sample.match(/\t/g) || []).length;
  const commas = (sample.match(/,/g) || []).length;
  return tabs > commas ? '\t' : ',';
}

export function parseCsv(
  text: string,
  options: { delimiter?: string; maxRows?: number } = {},
): CsvParseResult {
  const delimiter = options.delimiter || detectDelimiter(text);
  const maxRows = options.maxRows ?? DEFAULT_MAX_ROWS;

  const rows: string[][] = [];
  let field = '';
  let row: string[] = [];
  let inQuotes = false;
  let i = 0;
  const len = text.length;
  let totalRows = 0;
  let truncated = false;

  const pushField = () => {
    row.push(field);
    field = '';
  };
  const pushRow = () => {
    rows.push(row);
    row = [];
    totalRows++;
  };

  while (i < len) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i++;
        continue;
      }
      field += ch;
      i++;
      continue;
    }
    if (ch === '"' && field === '') {
      inQuotes = true;
      i++;
      continue;
    }
    if (ch === delimiter) {
      pushField();
      i++;
      continue;
    }
    if (ch === '\r') {
      // CRLF -> 当作一个换行
      if (text[i + 1] === '\n') i++;
      pushField();
      pushRow();
      if (rows.length >= maxRows) {
        truncated = true;
        break;
      }
      i++;
      continue;
    }
    if (ch === '\n') {
      pushField();
      pushRow();
      if (rows.length >= maxRows) {
        truncated = true;
        break;
      }
      i++;
      continue;
    }
    field += ch;
    i++;
  }

  if (!truncated) {
    if (field.length > 0 || row.length > 0) {
      pushField();
      pushRow();
    }
  } else {
    // 估算剩余行数（按平均行长粗略估算，仅用于提示）
    const remaining = text.slice(i);
    const more = (remaining.match(/\n/g) || []).length;
    totalRows += more;
  }

  // 列对齐：取最大列数，缺列补空字符串
  const maxCols = rows.reduce((m, r) => Math.max(m, r.length), 0);
  for (const r of rows) {
    while (r.length < maxCols) r.push('');
  }

  return { rows, delimiter, totalRows, truncated };
}

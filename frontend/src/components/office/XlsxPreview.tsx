import { useEffect, useMemo, useState } from 'react';
import { Empty, Spin, Table, Tabs } from 'antd';
import * as XLSX from 'xlsx';
import type { OfficePreviewProps } from './types';

const C = {
  bg: 'var(--jf-bg-panel)',
  bgDark: 'var(--jf-bg-deep)',
  text: 'var(--jf-text)',
  textSec: 'var(--jf-text-muted)',
  textDim: 'var(--jf-text-dim)',
  border: 'var(--jf-border)',
};

const MAX_PREVIEW_ROWS = 2000;

interface SheetView {
  name: string;
  header: string[];
  rows: string[][];
  totalRows: number;
  truncated: boolean;
}

function sheetToView(wb: XLSX.WorkBook, name: string): SheetView {
  const sheet = wb.Sheets[name];
  if (!sheet) {
    return { name, header: [], rows: [], totalRows: 0, truncated: false };
  }
  const aoa = XLSX.utils.sheet_to_json<string[]>(sheet, {
    header: 1,
    defval: '',
    raw: false,
  }) as string[][];
  const totalRows = aoa.length;
  const truncated = totalRows > MAX_PREVIEW_ROWS + 1;
  const sliced = truncated ? aoa.slice(0, MAX_PREVIEW_ROWS + 1) : aoa;
  if (sliced.length === 0) {
    return { name, header: [], rows: [], totalRows: 0, truncated: false };
  }
  const header = (sliced[0] || []).map((c, i) => String(c ?? '') || `col${i + 1}`);
  const rows = sliced.slice(1).map((r) => r.map((c) => String(c ?? '')));
  return { name, header, rows, totalRows: Math.max(0, totalRows - 1), truncated };
}

/**
 * .xlsx / .xls 纯前端预览（SheetJS）。
 * 多 sheet Tab + antd Table；超过 MAX_PREVIEW_ROWS 行截断提示。
 */
export default function XlsxPreview({ getArrayBuffer, fileName }: OfficePreviewProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [active, setActive] = useState<string>('');
  const [workbook, setWorkbook] = useState<XLSX.WorkBook | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setLoading(true);
      setError(null);
      try {
        const buf = await getArrayBuffer();
        if (cancelled) return;
        const wb = XLSX.read(buf, { type: 'array', cellDates: true });
        const names = wb.SheetNames || [];
        setWorkbook(wb);
        setSheetNames(names);
        setActive(names[0] || '');
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setWorkbook(null);
          setSheetNames([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [getArrayBuffer]);

  const view = useMemo(() => {
    if (!workbook || !active) return null;
    return sheetToView(workbook, active);
  }, [workbook, active]);

  if (loading) {
    return (
      <div style={centerStyle}>
        <Spin tip="正在解析表格…" />
      </div>
    );
  }

  if (error) {
    return (
      <div style={centerStyle}>
        <Empty
          description={
            <div style={{ color: C.textSec, fontSize: 13 }}>
              Excel 预览失败
              <div style={{ fontSize: 11, color: C.textDim, marginTop: 4 }}>
                {fileName ? `${fileName} · ` : ''}{error}
              </div>
            </div>
          }
        />
      </div>
    );
  }

  if (!view || sheetNames.length === 0) {
    return (
      <div style={centerStyle}>
        <Empty description="空工作簿" />
      </div>
    );
  }

  const columns = view.header.map((h, idx) => ({
    title: h,
    dataIndex: String(idx),
    key: String(idx),
    ellipsis: true,
    width: 160,
  }));
  const data = view.rows.map((r, ridx) => {
    const obj: Record<string, string> = { key: String(ridx) };
    r.forEach((cell, cidx) => {
      obj[String(cidx)] = cell;
    });
    return obj;
  });

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, background: C.bg }}>
      {sheetNames.length > 1 && (
        <Tabs
          size="small"
          activeKey={active}
          onChange={setActive}
          items={sheetNames.map((n) => ({ key: n, label: n }))}
          style={{ padding: '0 8px', flexShrink: 0 }}
        />
      )}
      <div
        style={{
          padding: '6px 14px',
          fontSize: 11,
          color: C.textDim,
          borderBottom: `1px solid ${C.border}`,
          display: 'flex',
          gap: 12,
          flexShrink: 0,
        }}
      >
        <span>工作表：{view.name}</span>
        <span>
          行数：{view.totalRows}
          {view.truncated ? `（仅显示前 ${MAX_PREVIEW_ROWS} 行）` : ''}
        </span>
        <span>列数：{view.header.length}</span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        {view.header.length === 0 ? (
          <div style={centerStyle}>
            <Empty description="空工作表" />
          </div>
        ) : (
          <Table
            size="small"
            columns={columns}
            dataSource={data}
            pagination={false}
            scroll={{ x: 'max-content' }}
            bordered
          />
        )}
      </div>
    </div>
  );
}

const centerStyle: React.CSSProperties = {
  flex: 1,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: C.bgDark,
  padding: 24,
};

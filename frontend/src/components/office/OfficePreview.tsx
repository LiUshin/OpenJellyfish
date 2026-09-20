import { useCallback, lazy, Suspense } from 'react';
import type { FileKind } from '../../utils/fileKind';
import RoutePending from '../RoutePending';
import ErrorBoundary from '../ErrorBoundary';
const DocxPreview = lazy(() => import('./DocxPreview'));
const XlsxPreview = lazy(() => import('./XlsxPreview'));
const PptxPreview = lazy(() => import('./PptxPreview'));
import type { OfficeBufferSource } from './types';

export type { OfficeBufferSource } from './types';

interface Props {
  kind: Extract<FileKind, 'docx' | 'xlsx' | 'pptx'>;
  getArrayBuffer: OfficeBufferSource;
  fileName?: string;
}

/** 按 kind 分发到对应纯前端 Office 预览器。 */
export default function OfficePreview({ kind, getArrayBuffer, fileName }: Props) {
  // 稳定引用：父组件若每次 inline 新函数会导致重复解析；这里仍信任父级 memo/callback
  const source = useCallback(() => getArrayBuffer(), [getArrayBuffer]);

  const Preview = kind === 'docx' ? DocxPreview : kind === 'xlsx' ? XlsxPreview : PptxPreview;
  return <ErrorBoundary key={kind} scope="office-preview"><Suspense fallback={<RoutePending />}><Preview getArrayBuffer={source} fileName={fileName} /></Suspense></ErrorBoundary>;
}

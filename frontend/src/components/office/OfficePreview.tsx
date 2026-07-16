import { useCallback } from 'react';
import type { FileKind } from '../../utils/fileKind';
import DocxPreview from './DocxPreview';
import XlsxPreview from './XlsxPreview';
import PptxPreview from './PptxPreview';
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

  if (kind === 'docx') return <DocxPreview getArrayBuffer={source} fileName={fileName} />;
  if (kind === 'xlsx') return <XlsxPreview getArrayBuffer={source} fileName={fileName} />;
  return <PptxPreview getArrayBuffer={source} fileName={fileName} />;
}

/**
 * 文件类型分类工具：根据文件名/扩展名判定渲染策略
 *
 * - media: 直接 <img>/<audio>/<video>/<iframe pdf> 渲染，不可编辑，不读取文本
 * - markdown / html: 支持「预览 / 源码」切换，默认预览，源码可编辑
 * - csv / json: 支持「预览 / 源码」切换，预览为表格 / 高亮树，源码可编辑
 * - text: 普通文本/代码，单一 textarea 编辑
 * - binary: 未知二进制，仅显示下载按钮
 */

export type FileKind =
  | 'image'
  | 'audio'
  | 'video'
  | 'pdf'
  | 'markdown'
  | 'html'
  | 'csv'
  | 'json'
  | 'text'
  | 'binary';

const EXT_KIND: Record<string, FileKind> = {
  // image
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image',
  webp: 'image', svg: 'image', bmp: 'image', ico: 'image',
  // audio
  mp3: 'audio', wav: 'audio', ogg: 'audio', m4a: 'audio',
  flac: 'audio', aac: 'audio', wma: 'audio',
  // video
  mp4: 'video', webm: 'video', mov: 'video', mkv: 'video',
  avi: 'video', ogv: 'video',
  // pdf
  pdf: 'pdf',
  // markdown
  md: 'markdown', markdown: 'markdown', mdx: 'markdown',
  // html
  html: 'html', htm: 'html',
  // csv
  csv: 'csv', tsv: 'csv',
  // json
  json: 'json', jsonl: 'json', ndjson: 'json',
  // generic text / code
  txt: 'text', log: 'text', diff: 'text', patch: 'text',
  py: 'text', pyw: 'text', ipynb: 'text',
  js: 'text', mjs: 'text', cjs: 'text', jsx: 'text',
  ts: 'text', tsx: 'text',
  c: 'text', h: 'text', cpp: 'text', cc: 'text', hpp: 'text',
  java: 'text', kt: 'text', go: 'text', rs: 'text',
  rb: 'text', php: 'text', swift: 'text',
  sql: 'text', graphql: 'text', proto: 'text',
  xml: 'text', yaml: 'text', yml: 'text', toml: 'text',
  ini: 'text', conf: 'text', cfg: 'text', env: 'text',
  sh: 'text', bash: 'text', zsh: 'text',
  ps1: 'text', bat: 'text', cmd: 'text',
  css: 'text', scss: 'text', less: 'text', sass: 'text',
};

const SPECIAL_TEXT_NAMES = new Set([
  'dockerfile',
  'makefile',
  'cmakelists.txt',
  '.gitignore',
  '.dockerignore',
  '.env',
  '.editorconfig',
  '.npmrc',
  '.prettierrc',
  '.eslintrc',
  'license',
  'readme',
]);

export function getFileKind(filename: string): FileKind {
  const lower = filename.toLowerCase();
  if (SPECIAL_TEXT_NAMES.has(lower)) return 'text';
  const dotIdx = lower.lastIndexOf('.');
  if (dotIdx <= 0) return 'binary';
  const ext = lower.slice(dotIdx + 1);
  return EXT_KIND[ext] || 'binary';
}

export function isMediaKind(kind: FileKind): boolean {
  return kind === 'image' || kind === 'audio' || kind === 'video' || kind === 'pdf';
}

/** 是否需要「预览 / 源码」切换头部按钮 */
export function isToggleKind(kind: FileKind): boolean {
  return kind === 'markdown' || kind === 'html' || kind === 'csv' || kind === 'json';
}

/** 是否需要读取文本内容（媒体类一律不读） */
export function shouldLoadText(kind: FileKind): boolean {
  return !isMediaKind(kind) && kind !== 'binary';
}

/** 工具栏是否显示「保存」按钮（媒体/binary 一律不显示） */
export function isEditableKind(kind: FileKind): boolean {
  return shouldLoadText(kind);
}

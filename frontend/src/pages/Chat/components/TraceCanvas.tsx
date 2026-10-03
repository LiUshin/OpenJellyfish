import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { ArrowCounterClockwise, ArrowSquareOut, ArrowsInSimple, ArrowsOutSimple, CaretLeft, CaretRight, CornersIn, FileText, Folder, Globe, Minus, Pause, Play, Plus, Stop, Terminal, X } from '@phosphor-icons/react';
import { mapTraceCoverage } from '../utils/tracePosition';
import styles from './traceCanvas.module.css';

export type TraceCanvasCell = 'read' | 'write' | 'both' | 'empty';

export interface TraceCanvasAction {
  id: string;
  callId?: string;
  name: string;
  kind: string;
  path?: string;
  detail?: string;
  result?: string;
  section?: string;
  complete?: boolean;
  succeeded?: boolean;
  input?: string;
  output?: string;
  diff?: string;
  oldText?: string;
  newText?: string;
  position?: { unit: 'line' | 'byte'; start: number; end: number; wholeFile?: boolean; boundary?: boolean; source: string };
  coverage?: {
    reads: Array<{ unit: 'line' | 'byte'; start: number; end: number; wholeFile?: boolean; boundary?: boolean; source: string }>;
    added: Array<{ unit: 'line' | 'byte'; start: number; end: number; wholeFile?: boolean; boundary?: boolean; source: string }>;
    deleted: Array<{ unit: 'line' | 'byte'; start: number; end: number; wholeFile?: boolean; boundary?: boolean; source: string }>;
    unknownRead: boolean;
    unknownWrite: boolean;
  };
}

export interface TraceCanvasNode {
  name: string;
  path: string;
  isDir: boolean;
  touched: number;
  children: Map<string, TraceCanvasNode>;
  actions: TraceCanvasAction[];
  positionCells: TraceCanvasCell[];
  unknownPositions?: number;
  unknownRead?: number;
  unknownWrite?: number;
  positionApproximate?: boolean;
  readCells?: boolean[];
  addedCells?: boolean[];
  deletedCells?: boolean[];
  totalLines?: number;
  totalBytes?: number;
}

export interface TraceCanvasTurn {
  id: string;
  question: string;
  answer: string;
  actions: TraceCanvasAction[];
}

interface TraceCanvasProps {
  nodes: TraceCanvasNode[];
  turns: TraceCanvasTurn[];
  from: number;
  to: number;
  onRangeChange: (from: number, to: number) => void;
  externalActions: TraceCanvasAction[];
  locale?: 'zh' | 'en';
  mode?: 'breadth' | 'timeline';
  onOpenFile?: (path: string) => void;
  fullscreen?: boolean;
  onToggleFullscreen?: () => void;
}

type CanvasView = { x: number; y: number; scale: number };
type PointerPoint = { x: number; y: number };
const MIN_SCALE = 0.03;
const MAX_SCALE = 2.5;

function clampScale(value: number) { return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value)); }

function contentView(width: number, height: number, viewportWidth: number, viewportHeight: number, mode: 'breadth' | 'timeline', all = false): CanvasView {
  const availableWidth = Math.max(100, viewportWidth - 56);
  const availableHeight = Math.max(100, viewportHeight - 56);
  // Timeline can be arbitrarily long. Its first screen should remain legible;
  // the explicit fit button is available when the user wants the whole route.
  const visibleHeight = all ? height : Math.min(height, mode === 'timeline' ? 930 : 1200);
  const measuredScale = clampScale(Math.min(1, availableWidth / width, availableHeight / visibleHeight));
  const scale = all ? measuredScale : Math.max(.32, measuredScale);
  return {
    x: Math.max(28, Math.round((viewportWidth - width * scale) / 2)),
    y: all && height * scale <= viewportHeight ? Math.round((viewportHeight - height * scale) / 2) : 28,
    scale,
  };
}

type CanvasItem = {
  id: string;
  kind: 'root' | 'folder' | 'file' | 'more';
  node?: TraceCanvasNode;
  parentId?: string;
  depth: number;
  x: number;
  y: number;
  w: number;
  h: number;
  hiddenCount?: number;
};

const X_STEP = 195;
const ROW_STEP = 92;
const MAX_NODES = 260;
const DEFAULT_SIBLINGS = 34;
const MAX_UNTOUCHED_SIBLINGS = 5;
const ROOT_ID = '__workspace_root__';

const copy = {
  zh: {
    workspace: '工作区', outside: '外部信息 · 工具返回', unknown: '位置未记录',
    approximation: '约略位置', recorded: '记录到的动作', empty: '此范围未记录触达',
    hidden: '条分支未展开', show: '展开更多', close: '收起',
    read: '读取', write: '写入', other: '操作', result: '返回',
    added: '新增', deleted: '删除', input: '调用输入', output: '工具返回', diff: '修改内容', oldText: '修改前', newText: '修改后',
    openFile: '在文件面板中打开', inspector: '动作详情', noDetail: '未记录具体输入或返回内容',
    position: '位置', approximateRange: '约第', line: '行', byte: '字节', wholeFile: '整个文件',
    timeline: '执行顺序', questionStep: '提问', answerStep: '回复', actions: '次操作',
    moreSteps: '继续显示后续步骤', selectedRange: '当前范围',
    recordedResultNote: '以下仅展示记录中的工具返回；若记录只有摘要，无法还原当时读取的全文。',
    rail: '提问范围导航', query: '问题', answer: '回复',
    railHint: '点击只看这一问 · Shift 点击选择连续范围',
    noAnswer: '尚无已记录回复', noOutside: '此范围没有外部动作',
    unknownCells: '十格均无可定位片段；访问次数显示在详情中',
    cells: '十格按记录的行号或 offset 标示大致位置；绿为读取，红为写入',
    touch: '次触达', failed: '未完成',
  },
  en: {
    workspace: 'Workspace', outside: 'External information · tool results', unknown: 'Position unrecorded',
    approximation: 'Approximate position', recorded: 'Recorded actions', empty: 'No recorded touch in this range',
    hidden: 'branches collapsed', show: 'Show more', close: 'Collapse',
    read: 'Read', write: 'Write', other: 'Action', result: 'Result',
    added: 'Added', deleted: 'Deleted', input: 'Tool input', output: 'Tool result', diff: 'Change', oldText: 'Before', newText: 'After',
    openFile: 'Open in file panel', inspector: 'Action details', noDetail: 'Detailed input or output was not recorded',
    position: 'Position', approximateRange: 'Approx. ', line: 'line', byte: 'byte', wholeFile: 'Whole file',
    timeline: 'Execution order', questionStep: 'Question', answerStep: 'Answer', actions: 'actions',
    moreSteps: 'Show more steps', selectedRange: 'Selected range',
    recordedResultNote: 'Only the recorded tool result is shown. A summary cannot reconstruct the full text read at the time.',
    rail: 'Query range navigation', query: 'Query', answer: 'Answer',
    railHint: 'Click for one query · Shift-click for a contiguous range',
    noAnswer: 'No recorded answer yet', noOutside: 'No external actions in this range',
    unknownCells: 'No locatable spans; counts appear in details',
    cells: 'Ten approximate positions from recorded line numbers or offsets; green is read, red is write',
    touch: 'touches', failed: 'Unsuccessful',
  },
};

function preview(value: string, limit = 130) {
  const compact = value.replace(/\s+/g, ' ').trim();
  return compact.length > limit ? `${compact.slice(0, limit - 1)}…` : compact;
}

function successful(action: TraceCanvasAction) {
  return action.succeeded !== false && action.complete !== false;
}

function normalizePath(value: string) {
  return value.replace(/\\/g, '/').replace(/\/{2,}/g, '/').replace(/^\.\//, '').replace(/\/$/, '').replace(/^\/+/, '');
}

// CLI actions may use a host-absolute path while the index uses a workspace path.
// Compare complete path suffixes with a segment boundary, never bare filenames.
function samePath(left: string, right: string) {
  const a = normalizePath(left);
  const b = normalizePath(right);
  if (!a || !b) return false;
  if (a === b) return true;
  // A single basename is ambiguous across the workspace; suffix matches need
  // at least one directory segment.
  return (b.includes('/') && a.endsWith(`/${b}`)) || (a.includes('/') && b.endsWith(`/${a}`));
}

function pathTouchesNode(actionPath: string, item: CanvasItem) {
  const nodePath = item.node?.path;
  if (!nodePath) return false;
  if (samePath(actionPath, nodePath)) return true;
  if (item.kind !== 'folder' && item.kind !== 'root') return false;
  const action = normalizePath(actionPath);
  const folder = normalizePath(nodePath);
  return !!folder && (action.startsWith(`${folder}/`) || action.includes(`/${folder}/`));
}

function sortChildren(children: Map<string, TraceCanvasNode>) {
  return [...children.values()].sort((a, b) =>
    Number(b.touched > 0) - Number(a.touched > 0) ||
    Number(b.isDir || b.children.size > 0) - Number(a.isDir || a.children.size > 0) ||
    a.name.localeCompare(b.name));
}

function layoutTree(
  roots: TraceCanvasNode[], expanded: Set<string>, collapsed: Set<string>, showAll: Set<string>, budget: number,
) {
  const items: CanvasItem[] = [];
  let nextLeafY = 70;
  let maxDepth = 0;

  function walk(node: TraceCanvasNode | undefined, parentId: string | undefined, depth: number, id: string): CanvasItem | null {
    if (items.length >= budget) return null;
    const kind: CanvasItem['kind'] = node ? node.isDir || node.children.size ? 'folder' : 'file' : 'root';
    const item: CanvasItem = {
      id, kind, node, parentId, depth, x: 24 + depth * X_STEP, y: 0,
      w: kind === 'file' ? 214 : kind === 'root' ? 166 : 178,
      h: kind === 'file' ? 83 : kind === 'root' ? 54 : 52,
    };
    items.push(item);
    maxDepth = Math.max(maxDepth, depth);
    const children = node ? sortChildren(node.children) : [...roots].sort((a, b) => Number(b.touched > 0) - Number(a.touched > 0) || a.name.localeCompare(b.name));
    const isOpen = !node || (!collapsed.has(node.path) && (expanded.has(node.path) || node.touched > 0));
    const visibleChildren: CanvasItem[] = [];
    if (isOpen && children.length) {
      let untouchedSeen = 0;
      let hidden = 0;
      let shown = 0;
      const all = showAll.has(node?.path ?? ROOT_ID);
      for (const child of children) {
        if (!all && (shown >= DEFAULT_SIBLINGS || (!child.touched && untouchedSeen >= MAX_UNTOUCHED_SIBLINGS))) {
          hidden++;
          continue;
        }
        if (items.length >= budget - 1) { hidden++; continue; }
        const rendered = walk(child, id, depth + 1, child.path);
        if (rendered) {
          visibleChildren.push(rendered);
          shown++;
          if (!child.touched) untouchedSeen++;
        } else hidden++;
      }
      if (hidden > 0) {
        const more: CanvasItem = {
          id: `${id}::__more`, kind: 'more', parentId: id, depth: depth + 1,
          x: 24 + (depth + 1) * X_STEP, y: nextLeafY, w: 178, h: 45, hiddenCount: hidden,
        };
        items.push(more);
        visibleChildren.push(more);
        nextLeafY += ROW_STEP;
        maxDepth = Math.max(maxDepth, depth + 1);
      }
    }
    if (visibleChildren.length) {
      const first = visibleChildren[0];
      const last = visibleChildren[visibleChildren.length - 1];
      // A balanced midpoint can put the workspace root many screens below the
      // viewport in large indexes. Keep each parent near its first children.
      item.y = Math.min(
        (first.y + first.h / 2 + last.y + last.h / 2) / 2 - item.h / 2,
        first.y + 180,
      );
    } else {
      item.y = nextLeafY;
      nextLeafY += ROW_STEP;
    }
    return item;
  }

  walk(undefined, undefined, 0, ROOT_ID);
  return { items, treeWidth: Math.max(530, 24 + (maxDepth + 1) * X_STEP), height: Math.max(540, nextLeafY + 28) };
}

function formatPosition(position: NonNullable<TraceCanvasAction['position']>, locale: 'zh' | 'en') {
  if (position.wholeFile) {
    if (/delete/i.test(position.source)) return locale === 'zh' ? '整文件删除' : 'Whole-file deletion';
    if (/write/i.test(position.source)) return locale === 'zh' ? '整文件写入（旧内容未记录）' : 'Whole-file write (prior contents unrecorded)';
    return locale === 'zh' ? '整个文件' : 'Whole file';
  }
  const start = position.unit === 'line' ? position.start + 1 : position.start;
  const end = position.unit === 'line' ? position.end : position.end - 1;
  if (position.boundary) return locale === 'zh' ? `约第 ${start} 行附近的删除边界` : `Deletion boundary near line ${start}`;
  const change = /diff|addition/i.test(position.source);
  const prefix = locale === 'zh' ? change ? '变更后第 ' : '第 '
    : change ? 'After change, ' : '';
  return locale === 'zh'
    ? `${prefix}${start}–${end} ${position.unit === 'line' ? '行' : '字节'}（调用记录）`
    : `${prefix}${position.unit === 'line' ? 'lines' : 'bytes'} ${start}–${end} (tool record)`;
}

function positionText(action: TraceCanvasAction, locale: 'zh' | 'en') {
  const coverage = action.coverage;
  if (coverage) {
    const wholeFileWrite = coverage.added.some(position => position.wholeFile);
    const groups = [
      [locale === 'zh' ? '读' : 'Read', coverage.reads],
      [wholeFileWrite ? locale === 'zh' ? '写入' : 'Written' : locale === 'zh' ? '新增' : 'Added', coverage.added],
      [locale === 'zh' ? '删除' : 'Deleted', coverage.deleted],
    ] as const;
    const descriptions = groups.flatMap(([label, positions]) => positions.map(position => `${label} ${formatPosition(position, locale)}`));
    if (descriptions.length) return descriptions.join(' · ');
  }
  return action.position ? formatPosition(action.position, locale) : locale === 'zh' ? '位置未记录' : 'Position unrecorded';
}

function Coverage({ node, locale, playedActions, currentActions }: {
  node: TraceCanvasNode; locale: 'zh' | 'en'; playedActions?: TraceCanvasAction[]; currentActions?: TraceCanvasAction[];
}) {
  const legacy = node.positionCells || [];
  const mapActions = (actions: TraceCanvasAction[]) => mapTraceCoverage(
    actions.filter(successful)
      .map(action => action.coverage).filter((coverage): coverage is NonNullable<TraceCanvasAction['coverage']> => !!coverage),
    { lines: node.totalLines, bytes: node.totalBytes });
  const mapped = playedActions === undefined ? null : mapActions(playedActions);
  const currentMap = currentActions === undefined ? null : mapActions(currentActions);
  const read = mapped?.readCells || node.readCells || legacy.map(cell => cell === 'read' || cell === 'both');
  const added = mapped?.addedCells || node.addedCells || legacy.map(cell => cell === 'write' || cell === 'both');
  const deleted = mapped?.deletedCells || node.deletedCells || [];
  const visibleActions = playedActions ?? node.actions;
  const wholeFileWrite = visibleActions.some(action => successful(action) && action.coverage?.added.some(position => position.wholeFile));
  const rows = [
    { key: 'read', cells: read, current: currentMap?.readCells, label: locale === 'zh' ? '读取' : 'Read' },
    { key: 'added', cells: added, current: currentMap?.addedCells, label: wholeFileWrite ? locale === 'zh' ? '写入（旧内容未知）' : 'Written (prior contents unknown)' : locale === 'zh' ? '新增' : 'Added' },
    { key: 'deleted', cells: deleted, current: currentMap?.deletedCells, label: locale === 'zh' ? '删除' : 'Deleted' },
  ].filter(row => row.cells.some(Boolean));
  const hasKnown = rows.some(row => row.cells.some(Boolean));
  const rangeLabels = visibleActions.filter(action => successful(action) && (action.position || action.coverage)).map(action => positionText(action, locale));
  const segmentTotal = node.totalLines || node.totalBytes;
  const segmentUnit = node.totalLines ? locale === 'zh' ? '行' : 'lines' : locale === 'zh' ? '字节' : 'bytes';
  return <span className={styles.coverage} role="img" aria-label={`${locale === 'zh' ? '文件位置' : 'File positions'}: ${rangeLabels.join(' · ') || (locale === 'zh' ? '未记录' : 'Unrecorded')}`}>
    {rows.map(row => <span key={row.key} className={`${styles.coverageRow} ${styles[`coverage_${row.key}`]}`}
      title={`${row.label} · ${rangeLabels.join(' · ') || (locale === 'zh' ? '位置未记录' : 'Position unrecorded')}`}>
      <small>{row.key === 'read' ? 'R' : row.key === 'added' ? wholeFileWrite ? 'W' : '+' : '−'}</small>
      <span className={styles.coverageSegments}>
        {Array.from({ length: 10 }, (_, index) => <span key={index}
          className={`${styles.coverageCell} ${row.cells[index] ? styles.coverageFilled : ''} ${row.current?.[index] ? styles.coverageFresh : ''}`}
          title={`${row.label} · ${locale === 'zh' ? `文件的第 ${index + 1}/10 段` : `Segment ${index + 1}/10`}${segmentTotal ? ` · ${locale === 'zh' ? '约第' : 'approx.'} ${Math.floor(index * segmentTotal / 10) + 1}–${Math.max(1, Math.ceil((index + 1) * segmentTotal / 10))} ${segmentUnit}` : ''}`} />)}
      </span>
    </span>)}
    {!hasKnown && (playedActions !== undefined ? !!playedActions.length : !!(node.unknownPositions || node.unknownRead || node.unknownWrite)) ? <span className={styles.coverageUnknown}>{locale === 'zh' ? '位置未记录' : 'Unlocated'}</span> : null}
  </span>;
}

type Inspected = { kind: 'node'; node: TraceCanvasNode } | { kind: 'action'; action: TraceCanvasAction } |
  { kind: 'actionGroup'; actions: TraceCanvasAction[] } | { kind: 'turn'; turn: TraceCanvasTurn; number: number };

function bounded(value: string) {
  const limit = 80000;
  return value.length > limit ? `${value.slice(0, limit)}\n…` : value;
}

function Inspector({ inspected, locale, onClose, onOpenFile, resolveActionFilePath }: {
  inspected: Inspected; locale: 'zh' | 'en'; onClose: () => void; onOpenFile?: (path: string) => void;
  resolveActionFilePath: (action: TraceCanvasAction) => string | undefined;
}) {
  const labels = copy[locale];
  const title = inspected.kind === 'node' ? inspected.node.name : inspected.kind === 'action' ? inspected.action.name
    : inspected.kind === 'actionGroup' ? inspected.actions[0]?.name || labels.inspector : `Q${inspected.number}`;
  const path = inspected.kind === 'node' ? inspected.node.path : inspected.kind === 'action' ? inspected.action.path : undefined;
  const openPath = inspected.kind === 'node' ? inspected.node.path : inspected.kind === 'action' ? resolveActionFilePath(inspected.action) : undefined;
  const actions = inspected.kind === 'node' ? inspected.node.actions : inspected.kind === 'action' ? [inspected.action]
    : inspected.kind === 'actionGroup' ? inspected.actions : inspected.turn.actions;
  return <aside className={styles.inspector} aria-label={labels.inspector}>
    <div className={styles.inspectorHead}><div><small>{labels.inspector}</small><h3 title={title}>{title}</h3></div>
      <button type="button" className={styles.inspectorClose} onClick={onClose} aria-label={labels.close}><X size={17} /></button></div>
    {path && <div className={styles.inspectorPath}><span>{path}</span>{onOpenFile && openPath && <button type="button" onClick={() => onOpenFile(openPath)}><ArrowSquareOut size={15} />{labels.openFile}</button>}</div>}
    {inspected.kind === 'turn' && <><div className={styles.inspectorSection}><strong>{labels.questionStep}</strong><p>{inspected.turn.question}</p></div><div className={styles.inspectorSection}><strong>{labels.answerStep}</strong><p>{inspected.turn.answer || labels.noAnswer}</p></div></>}
    {!actions.length && <p className={styles.inspectorEmpty}>{labels.noDetail}</p>}
    {actions.map((action, index) => <details key={`${action.id}-${index}`} className={styles.inspectorAction} open={index === 0 || undefined}>
      <summary><span>{String(index + 1).padStart(2, '0')}</span><strong>{action.name}</strong><small>{action.kind}{action.succeeded === false ? ` · ${labels.failed}` : ''}</small></summary>
      {action.path && <div className={styles.inspectorMeta}>{action.path}{onOpenFile && resolveActionFilePath(action) &&
        <button type="button" className={styles.inspectorInlineOpen} onClick={() => onOpenFile(resolveActionFilePath(action)!)}
          aria-label={`${labels.openFile}: ${action.path}`} title={labels.openFile}><ArrowSquareOut size={13} /></button>}</div>}
      <div className={styles.inspectorMeta}>{labels.position}: {positionText(action, locale)}</div>
      {action.section && <div className={styles.inspectorMeta}>{action.section}</div>}
      {action.input && <div className={styles.inspectorSection}><strong>{labels.input}</strong><pre>{bounded(action.input)}</pre></div>}
      {action.diff && <div className={styles.inspectorSection}><strong>{labels.diff}</strong><pre className={styles.inspectorDiff}>{bounded(action.diff)}</pre></div>}
      {action.oldText && <div className={styles.inspectorSection}><strong>{labels.oldText}</strong><pre className={styles.inspectorDeleted}>{bounded(action.oldText)}</pre></div>}
      {action.newText && <div className={styles.inspectorSection}><strong>{labels.newText}</strong><pre className={styles.inspectorAdded}>{bounded(action.newText)}</pre></div>}
      {(action.output || action.result) && <div className={styles.inspectorSection}><strong>{labels.output}</strong><pre>{bounded(action.output || action.result || '')}</pre></div>}
      {action.kind === 'read' && <p className={styles.inspectorEmpty}>{labels.recordedResultNote}</p>}
      {!action.input && !action.diff && !action.oldText && !action.newText && !action.output && !action.result && <p className={styles.inspectorEmpty}>{labels.noDetail}</p>}
    </details>)}
  </aside>;
}

function ExternalIcon({ kind }: { kind: string }) {
  if (kind === 'command') return <Terminal size={16} weight="duotone" />;
  if (kind === 'search' || kind === 'external') return <Globe size={16} weight="duotone" />;
  return <FileText size={16} weight="duotone" />;
}

type PlaybackStep = {
  key: string;
  kind: 'question' | 'action' | 'answer';
  number: number;
  turn: TraceCanvasTurn;
  actions: TraceCanvasAction[];
};

function playbackStepsFor(turns: TraceCanvasTurn[], from: number, to: number): PlaybackStep[] {
  const steps: PlaybackStep[] = [];
  turns.slice(from, to + 1).forEach((turn, localIndex) => {
    const number = from + localIndex + 1;
    steps.push({ key: `${turn.id}-question`, kind: 'question', number, turn, actions: [] });
    const grouped = new Map<string, PlaybackStep>();
    turn.actions.forEach((action, index) => {
      // A single tool call can report several affected files. Their records
      // share callId and have no meaningful order within that invocation.
      const groupKey = action.callId ? `call:${action.callId}` : `record:${index}`;
      let step = grouped.get(groupKey);
      if (!step) {
        step = { key: `${turn.id}-action-${groupKey}`, kind: 'action', number, turn, actions: [] };
        grouped.set(groupKey, step);
        steps.push(step);
      }
      step.actions.push(action);
    });
    steps.push({ key: `${turn.id}-answer`, kind: 'answer', number, turn, actions: [] });
  });
  return steps;
}

type TimelineStep = PlaybackStep & {
  key: string;
  x: number;
  y: number;
  w: number;
  h: number;
};

function layoutTimeline(playbackSteps: PlaybackStep[], limit: number): TimelineStep[] {
  return playbackSteps.slice(0, limit).map((step, ordinal) => {
    const x = step.kind === 'action' ? 182 : 118;
    const w = step.kind === 'action' ? 480 : 440;
    return { ...step, x, y: 74 + ordinal * 128, w, h: step.kind === 'action' ? 100 : 88 };
  });
}

export default function TraceCanvas({ nodes, turns, from, to, onRangeChange, externalActions,
  locale = 'zh', mode = 'breadth', onOpenFile, fullscreen = false, onToggleFullscreen }: TraceCanvasProps) {
  const labels = copy[locale];
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [showAll, setShowAll] = useState<Set<string>>(() => new Set());
  const [budget, setBudget] = useState(MAX_NODES);
  const [externalLimit, setExternalLimit] = useState(32);
  const [timelineLimit, setTimelineLimit] = useState(120);
  const [playbackIndex, setPlaybackIndex] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [hoveredQuery, setHoveredQuery] = useState<number | null>(null);
  const [queryTooltipTop, setQueryTooltipTop] = useState(34);
  const [inspected, setInspected] = useState<Inspected | null>(null);
  const [view, setView] = useState<CanvasView>({ x: 28, y: 28, scale: 1 });
  const [dragging, setDragging] = useState(false);
  const [wheeling, setWheeling] = useState(false);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const rangeAnchor = useRef<number | null>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const pointers = useRef<Map<number, PointerPoint>>(new Map());
  const viewRef = useRef(view);
  const lastViewport = useRef({ width: 0, height: 0 });
  const framedWidth = useRef(0);
  const lastMode = useRef<TraceCanvasProps['mode']>(undefined);
  const lastRange = useRef('');
  const suppressBackgroundClick = useRef(false);
  const wheelIdleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => { setInspected(null); }, [from, to]);
  useEffect(() => { setPlaybackIndex(null); setPlaying(false); }, [from, to, turns]);
  const { items, treeWidth, height } = useMemo(
    () => layoutTree(nodes, expanded, collapsed, showAll, budget),
    [nodes, expanded, collapsed, showAll, budget],
  );
  const externalX = Math.max(treeWidth + 16, ...items.map(item => item.x + item.w + 18));
  const externalShown = useMemo(() => externalActions.slice(0, externalLimit), [externalActions, externalLimit]);
  const boardWidth = externalX + 236;
  const boardHeight = Math.max(height, 100 + (externalShown.length + (externalActions.length > externalLimit ? 1 : 0)) * 78);
  const itemById = useMemo(() => new Map(items.map(item => [item.id, item])), [items]);
  const hoverPaths = hoveredQuery === null ? null : (turns[hoveredQuery]?.actions || []).flatMap(action => action.path ? [action.path] : []);
  const hoverActionIds = hoveredQuery === null ? null : new Set(turns[hoveredQuery]?.actions.map(action => action.id) || []);
  const playbackSteps = useMemo(() => playbackStepsFor(turns, from, to), [turns, from, to]);
  const timelineSteps = useMemo(() => layoutTimeline(playbackSteps, timelineLimit), [playbackSteps, timelineLimit]);
  const totalTimelineSteps = playbackSteps.length;
  const timelineHeight = Math.max(560, (timelineSteps.length + (totalTimelineSteps > timelineSteps.length ? 1 : 0)) * 128 + 88);
  const timelineWidth = 910;
  const contentWidth = mode === 'breadth' ? boardWidth : timelineWidth;
  const contentHeight = mode === 'breadth' ? boardHeight : timelineHeight;
  const currentStep = playbackIndex === null ? null : playbackSteps[playbackIndex] || null;
  const currentActionIds = new Set(currentStep?.actions.map(action => action.id) || []);
  const playedActions = playbackIndex === null ? null : playbackSteps.slice(0, playbackIndex + 1).flatMap(step => step.actions);
  const playedSuccessfulIds = new Set(playedActions?.filter(successful).map(action => action.id) || []);

  useEffect(() => {
    if (!playing || playbackIndex === null) return;
    if (playbackIndex >= playbackSteps.length - 1) { setPlaying(false); return; }
    const timer = setTimeout(() => setPlaybackIndex(index => index === null ? 0 : index + 1), 1300);
    return () => clearTimeout(timer);
  }, [playing, playbackIndex, playbackSteps.length]);

  useEffect(() => {
    if (playbackIndex === null) return;
    if (mode === 'timeline' && playbackIndex >= timelineLimit) setTimelineLimit(playbackIndex + 20);
    const current = playbackSteps[playbackIndex];
    if (!current) return;
    const neededExternal = current.actions.map(action => externalActions.findIndex(item => item.id === action.id))
      .reduce((max, index) => Math.max(max, index), -1);
    if (neededExternal >= externalLimit) setExternalLimit(neededExternal + 1);
  }, [playbackIndex, mode, timelineLimit, playbackSteps, externalActions, externalLimit]);

  function updateView(next: CanvasView) {
    viewRef.current = next;
    setView(next);
  }

  function zoomAt(factor: number, x: number, y: number) {
    const current = viewRef.current;
    const scale = clampScale(current.scale * factor);
    if (scale === current.scale) return;
    updateView({
      x: x - ((x - current.x) / current.scale) * scale,
      y: y - ((y - current.y) / current.scale) * scale,
      scale,
    });
  }

  function fitContent() {
    if (!viewportSize.width || !viewportSize.height) return;
    updateView(contentView(contentWidth, contentHeight, viewportSize.width, viewportSize.height, mode, true));
  }

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const resizeObserver = new ResizeObserver(entries => {
      const rect = entries[0]?.contentRect;
      if (rect) setViewportSize({ width: rect.width, height: rect.height });
    });
    resizeObserver.observe(viewport);
    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    if (!viewportSize.width || !viewportSize.height) return;
    const previous = lastViewport.current;
    const rangeKey = `${from}:${to}`;
    if (lastMode.current !== mode || lastRange.current !== rangeKey || !previous.width) {
      updateView(contentView(contentWidth, contentHeight, viewportSize.width, viewportSize.height, mode));
      lastMode.current = mode;
      lastRange.current = rangeKey;
      framedWidth.current = viewportSize.width;
    } else if (previous.width !== viewportSize.width || previous.height !== viewportSize.height) {
      // Compare against the last framed width so animated pane resizes cannot
      // evade the threshold through a series of small intermediate steps.
      const widthRatio = viewportSize.width / (framedWidth.current || previous.width);
      if (widthRatio < .75 || widthRatio > 1.33) {
        // Opening the file panel can halve the middle pane. Reframe the route
        // so its cards do not disappear outside the new, narrower viewport.
        updateView(contentView(contentWidth, contentHeight, viewportSize.width, viewportSize.height, mode));
        framedWidth.current = viewportSize.width;
      } else {
        // Small resizes and fullscreen transitions keep the same world point
        // at the centre while the viewport grows or contracts.
        const current = viewRef.current;
        updateView({ ...current, x: current.x + (viewportSize.width - previous.width) / 2,
          y: current.y + (viewportSize.height - previous.height) / 2 });
      }
    }
    lastViewport.current = viewportSize;
  }, [mode, from, to, contentWidth, contentHeight, viewportSize]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      setWheeling(true);
      if (wheelIdleTimer.current) clearTimeout(wheelIdleTimer.current);
      wheelIdleTimer.current = setTimeout(() => setWheeling(false), 140);
      const rect = viewport.getBoundingClientRect();
      const multiplier = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16 : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? rect.height : 1;
      const factor = Math.exp(-event.deltaY * multiplier * (event.ctrlKey ? .003 : .0015));
      zoomAt(factor, event.clientX - rect.left, event.clientY - rect.top);
    };
    viewport.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      viewport.removeEventListener('wheel', onWheel);
      if (wheelIdleTimer.current) clearTimeout(wheelIdleTimer.current);
    };
  }, []);

  function pointerPoint(event: ReactPointerEvent<HTMLDivElement>): PointerPoint {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function pointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    // Buttons, file open actions, and inspector controls retain their usual
    // click, focus, and keyboard behaviour.
    if ((event.target as HTMLElement).closest('button, a, summary, details, [data-canvas-ui]')) return;
    event.currentTarget.focus({ preventScroll: true });
    pointers.current.set(event.pointerId, pointerPoint(event));
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function pointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const previous = pointers.current.get(event.pointerId);
    if (!previous) return;
    const currentPoint = pointerPoint(event);
    const previousPoints = [...pointers.current.values()];
    pointers.current.set(event.pointerId, currentPoint);
    if (pointers.current.size > 1 && previousPoints.length > 1) {
      const currentPoints = [...pointers.current.values()];
      const a = previousPoints[0]; const b = previousPoints[1];
      const c = currentPoints[0]; const d = currentPoints[1];
      const oldMid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const newMid = { x: (c.x + d.x) / 2, y: (c.y + d.y) / 2 };
      const oldDistance = Math.hypot(a.x - b.x, a.y - b.y);
      const newDistance = Math.hypot(c.x - d.x, c.y - d.y);
      const current = viewRef.current;
      const scale = clampScale(current.scale * (oldDistance ? newDistance / oldDistance : 1));
      updateView({ x: newMid.x - (oldMid.x - current.x) * scale / current.scale,
        y: newMid.y - (oldMid.y - current.y) * scale / current.scale, scale });
    } else {
      const dx = currentPoint.x - previous.x;
      const dy = currentPoint.y - previous.y;
      if (dx || dy) updateView({ ...viewRef.current, x: viewRef.current.x + dx, y: viewRef.current.y + dy });
    }
    if (Math.abs(currentPoint.x - previous.x) + Math.abs(currentPoint.y - previous.y) > 1) suppressBackgroundClick.current = true;
  }

  function pointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    pointers.current.delete(event.pointerId);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (!pointers.current.size) setDragging(false);
  }

  function canvasKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return;
    const move: Record<string, [number, number]> = {
      ArrowLeft: [72, 0], ArrowRight: [-72, 0], ArrowUp: [0, 72], ArrowDown: [0, -72],
    };
    if (move[event.key]) {
      event.preventDefault();
      const [dx, dy] = move[event.key];
      updateView({ ...viewRef.current, x: viewRef.current.x + dx, y: viewRef.current.y + dy });
    } else if (event.key === '+' || event.key === '=') {
      event.preventDefault(); zoomAt(1.25, viewportSize.width / 2, viewportSize.height / 2);
    } else if (event.key === '-') {
      event.preventDefault(); zoomAt(.8, viewportSize.width / 2, viewportSize.height / 2);
    } else if (event.key === '0') {
      event.preventDefault(); fitContent();
    }
  }
  function startPlayback() {
    if (!playbackSteps.length) return;
    if (playbackIndex === null || playbackIndex >= playbackSteps.length - 1) setPlaybackIndex(0);
    setPlaying(true);
  }
  function stepPlayback(direction: -1 | 1) {
    if (!playbackSteps.length) return;
    setPlaying(false);
    setPlaybackIndex(index => index === null ? 0 : Math.max(0, Math.min(playbackSteps.length - 1, index + direction)));
  }
  const playbackLabel = !currentStep ? (locale === 'zh' ? '按播放依次点亮记录' : 'Play the recorded sequence')
    : currentStep.kind === 'question' ? `Q${currentStep.number} · ${locale === 'zh' ? '提问' : 'Query'}`
      : currentStep.kind === 'answer' ? `Q${currentStep.number} · ${locale === 'zh' ? '回复' : 'Answer'}`
        : `Q${currentStep.number} · ${currentStep.actions[0]?.name || (locale === 'zh' ? '工具调用' : 'Tool call')}${currentStep.actions.length > 1 ? ` · ${currentStep.actions.length} ${locale === 'zh' ? '处路径' : 'paths'}` : ''}${currentStep.actions.some(action => !successful(action)) ? locale === 'zh' ? ' · 未成功' : ' · unsuccessful' : ''}`;
  const workspaceFilePaths = useMemo(() => {
    const paths: string[] = [];
    const walk = (node: TraceCanvasNode) => {
      if (!node.isDir && node.children.size === 0) paths.push(node.path);
      node.children.forEach(walk);
    };
    nodes.forEach(walk);
    return paths;
  }, [nodes]);

  const fileNodes = useMemo(() => {
    const files: TraceCanvasNode[] = [];
    const walk = (node: TraceCanvasNode) => {
      if (!node.isDir && !node.children.size) files.push(node);
      node.children.forEach(walk);
    };
    nodes.forEach(walk);
    return files;
  }, [nodes]);

  function resolveActionFilePath(action: TraceCanvasAction) {
    const raw = action.path;
    if (!raw || /^\w+:\/\//.test(raw)) return undefined;
    const match = workspaceFilePaths.find(path => samePath(path, raw));
    if (match) return match;
    // File tools can refer to a file not returned by a partial workspace index.
    if (['read', 'write', 'output'].includes(action.kind) && (raw.includes('/') || raw.includes('\\') || /\.[a-z0-9]{1,8}$/i.test(raw))) return raw;
    return undefined;
  }

  function fileNodeForAction(action: TraceCanvasAction) {
    return action.path ? fileNodes.find(node => samePath(action.path!, node.path)) : undefined;
  }

  function playbackNodeState(item: CanvasItem): 'current' | 'failed' | 'visited' | 'pending' | null {
    if (playbackIndex === null) return null;
    if (item.kind === 'root') return currentStep?.kind !== 'action' ? 'current' : 'visited';
    const hits = (action: TraceCanvasAction) => !!(
      (action.path && pathTouchesNode(action.path, item)) || item.node?.actions.some(record => record.id === action.id)
    );
    if (currentStep?.actions.some(action => successful(action) && hits(action))) return 'current';
    if (currentStep?.actions.some(hits)) return 'failed';
    if (playedActions?.some(action => successful(action) && hits(action))) return 'visited';
    return 'pending';
  }

  useEffect(() => {
    if (playbackIndex === null || !currentStep || !viewportSize.width || !viewportSize.height) return;
    const targets: Array<{ x: number; y: number; w: number; h: number }> = [];
    if (mode === 'timeline') {
      const step = timelineSteps[playbackIndex];
      if (step) targets.push(step);
    } else if (currentStep.kind !== 'action') {
      const root = items.find(item => item.kind === 'root');
      if (root) targets.push(root);
    } else {
      currentStep.actions.forEach(action => {
        const matches = action.path ? items.filter(item => item.kind !== 'more' && pathTouchesNode(action.path!, item)) : [];
        const visible = matches.sort((a, b) => b.depth - a.depth)[0];
        if (visible) targets.push(visible);
        else {
          const index = externalShown.findIndex(record => record.id === action.id);
          if (index >= 0) targets.push({ x: externalX, y: 68 + index * 78, w: 220, h: 61 });
        }
      });
    }
    if (!targets.length) return;
    const left = Math.min(...targets.map(target => target.x));
    const top = Math.min(...targets.map(target => target.y));
    const right = Math.max(...targets.map(target => target.x + target.w));
    const bottom = Math.max(...targets.map(target => target.y + target.h));
    const centreX = (left + right) / 2;
    const centreY = (top + bottom) / 2;
    const current = viewRef.current;
    const screenX = current.x + centreX * current.scale;
    const screenY = current.y + centreY * current.scale;
    const marginX = Math.min(130, viewportSize.width * .24);
    const marginY = Math.min(110, viewportSize.height * .22);
    if (screenX >= marginX && screenX <= viewportSize.width - marginX &&
      screenY >= marginY && screenY <= viewportSize.height - marginY) return;
    updateView({ ...current, x: viewportSize.width / 2 - centreX * current.scale,
      y: viewportSize.height / 2 - centreY * current.scale });
  }, [playbackIndex, mode, currentStep, items, timelineSteps, externalShown, externalX, viewportSize]);

  function highlighted(item: CanvasItem) {
    if (playbackIndex !== null) return ['current', 'visited', 'failed'].includes(playbackNodeState(item) || '');
    if (item.kind === 'root') return true;
    if (hoverPaths) return hoverPaths.some(path => pathTouchesNode(path, item));
    return !!item.node?.touched;
  }

  function toggleFolder(node: TraceCanvasNode) {
    const open = !collapsed.has(node.path) && (expanded.has(node.path) || node.touched > 0);
    if (open) {
      setCollapsed(previous => new Set(previous).add(node.path));
    } else {
      setCollapsed(previous => { const next = new Set(previous); next.delete(node.path); return next; });
      setExpanded(previous => new Set(previous).add(node.path));
    }
  }

  function showMore(parentId: string) {
    setShowAll(previous => new Set(previous).add(parentId));
    setBudget(previous => previous + 220);
  }

  function showQuery(index: number, target?: HTMLElement) {
    setHoveredQuery(index);
    if (target && shellRef.current) {
      setQueryTooltipTop(Math.max(8, Math.min(target.getBoundingClientRect().top - shellRef.current.getBoundingClientRect().top - 10, shellRef.current.clientHeight - 130)));
    }
  }

  function chooseQuery(index: number, extend: boolean) {
    if (extend) {
      const anchor = rangeAnchor.current ?? from;
      onRangeChange(Math.min(anchor, index), Math.max(anchor, index));
    } else {
      rangeAnchor.current = index;
      onRangeChange(index, index);
    }
  }

  function queryKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === 'Escape') { setHoveredQuery(null); return; }
    const next = event.key === 'ArrowDown' ? Math.min(turns.length - 1, index + 1)
      : event.key === 'ArrowUp' ? Math.max(0, index - 1)
      : event.key === 'Home' ? 0 : event.key === 'End' ? turns.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[data-trace-query="${next}"]`)?.focus();
  }

  const selectedQ = hoveredQuery !== null ? turns[hoveredQuery] : undefined;
  return <div className={`${styles.shell} ${fullscreen ? styles.fullscreen : ''}`} ref={shellRef}>
    <nav className={styles.queryRail} aria-label={labels.rail} onPointerLeave={() => setHoveredQuery(null)}
      onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setHoveredQuery(null); }}>
      {turns.map((turn, index) => <button key={turn.id} type="button" data-trace-query={index}
        className={`${styles.queryBar} ${index >= from && index <= to ? styles.querySelected : ''} ${hoveredQuery === index ? styles.queryHovered : ''} ${currentStep?.number === index + 1 ? styles.playbackQueryCurrent : ''}`}
        aria-label={`Q${index + 1} · ${preview(turn.question, 100)}`}
        aria-current={from === to && from === index ? 'step' : undefined}
        onPointerEnter={event => showQuery(index, event.currentTarget)} onFocus={event => showQuery(index, event.currentTarget)}
        onKeyDown={event => queryKeyDown(event, index)} onClick={event => chooseQuery(index, event.shiftKey)}>
        <span aria-hidden="true" />
      </button>)}
    </nav>
    {selectedQ && <div className={styles.queryTooltip} style={{ top: queryTooltipTop }} role="status">
      <strong>Q{hoveredQuery! + 1} · {preview(selectedQ.question || '—', 120)}</strong>
      <span>{labels.answer}: {preview(selectedQ.answer || labels.noAnswer, 130)}</span>
      <small>{labels.railHint}</small>
    </div>}

    <div ref={viewportRef} tabIndex={0} role="region"
      className={`${styles.scroll} ${dragging ? styles.dragging : ''} ${wheeling ? styles.wheeling : ''}`}
      aria-label={mode === 'breadth' ? labels.workspace : labels.timeline}
      onKeyDown={canvasKeyDown}
      onClick={event => {
        if (event.target !== event.currentTarget) return;
        if (suppressBackgroundClick.current) { suppressBackgroundClick.current = false; return; }
        setInspected(null);
      }}
      onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
      <div className={styles.playbackControls} data-canvas-ui role="group"
        aria-label={locale === 'zh' ? '轨迹逐步回放' : 'Step through trace'}
        title={locale === 'zh' ? '按记录顺序逐步点亮；播放间隔仅用于展示，不代表实际耗时。' : 'Steps follow record order; playback timing is illustrative, not actual duration.'}>
        <button type="button" disabled={!playbackSteps.length} onClick={() => { setPlaybackIndex(0); setPlaying(true); }}
          aria-label={locale === 'zh' ? '从头回放轨迹' : 'Restart trace playback'} title={locale === 'zh' ? '从头回放' : 'Restart'}><ArrowCounterClockwise size={15} /></button>
        <button type="button" disabled={!playbackSteps.length} onClick={() => stepPlayback(-1)}
          aria-label={locale === 'zh' ? '上一步轨迹' : 'Previous trace step'} title={locale === 'zh' ? '上一步' : 'Previous step'}><CaretLeft size={15} /></button>
        <button type="button" disabled={!playbackSteps.length} onClick={() => playing ? setPlaying(false) : startPlayback()}
          aria-label={playing ? locale === 'zh' ? '暂停轨迹回放' : 'Pause trace playback' : locale === 'zh' ? '播放轨迹' : 'Play trace'}
          title={playing ? locale === 'zh' ? '暂停' : 'Pause' : locale === 'zh' ? '播放' : 'Play'}>
          {playing ? <Pause size={15} weight="fill" /> : <Play size={15} weight="fill" />}
        </button>
        <button type="button" disabled={!playbackSteps.length} onClick={() => stepPlayback(1)}
          aria-label={locale === 'zh' ? '下一步轨迹' : 'Next trace step'} title={locale === 'zh' ? '下一步' : 'Next step'}><CaretRight size={15} /></button>
        {playbackIndex !== null && <button type="button" onClick={() => { setPlaying(false); setPlaybackIndex(null); }}
          aria-label={locale === 'zh' ? '结束轨迹回放' : 'Stop trace playback'} title={locale === 'zh' ? '结束回放，显示全部' : 'Stop and show all'}><Stop size={14} /></button>}
        <span className={styles.playbackCounter} aria-live="polite" role="status">
          {playbackIndex === null ? `—/${playbackSteps.length}` : `${playbackIndex + 1}/${playbackSteps.length}`}
          <span className={styles.playbackLabel}> · {playbackLabel}</span>
        </span>
      </div>
      <div className={styles.canvasControls} data-canvas-ui>
        <button type="button" onClick={() => zoomAt(.8, viewportSize.width / 2, viewportSize.height / 2)}
          aria-label={locale === 'zh' ? '缩小画布' : 'Zoom out'} title={locale === 'zh' ? '缩小' : 'Zoom out'}><Minus size={15} /></button>
        <span className={styles.zoomLevel} aria-label={locale === 'zh' ? `缩放 ${Math.round(view.scale * 100)}%` : `Zoom ${Math.round(view.scale * 100)}%`}>{Math.round(view.scale * 100)}%</span>
        <button type="button" onClick={() => zoomAt(1.25, viewportSize.width / 2, viewportSize.height / 2)}
          aria-label={locale === 'zh' ? '放大画布' : 'Zoom in'} title={locale === 'zh' ? '放大' : 'Zoom in'}><Plus size={15} /></button>
        <span className={styles.controlDivider} />
        <button type="button" onClick={fitContent} aria-label={locale === 'zh' ? '适合画布大小' : 'Fit canvas'}
          title={locale === 'zh' ? '适合画布大小' : 'Fit canvas'}><CornersIn size={15} /></button>
        {onToggleFullscreen && <button type="button" onClick={onToggleFullscreen}
          aria-label={fullscreen ? locale === 'zh' ? '退出画布全屏' : 'Exit canvas fullscreen' : locale === 'zh' ? '画布全屏' : 'Fullscreen canvas'}
          aria-pressed={fullscreen} title={fullscreen ? locale === 'zh' ? '退出全屏' : 'Exit fullscreen' : locale === 'zh' ? '全屏' : 'Fullscreen'}>
          {fullscreen ? <ArrowsInSimple size={15} /> : <ArrowsOutSimple size={15} />}
        </button>}
      </div>
      <div className={styles.viewportHint} data-canvas-ui>{locale === 'zh' ? '拖动浏览 · 滚轮缩放' : 'Drag to pan · Scroll to zoom'}</div>
      <div className={styles.stage} style={{ transform: `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.scale})` }}>
      <div key={mode} className={`${styles.board} ${styles.scene}`} style={{ width: contentWidth, height: contentHeight }}
        onClick={event => {
          if (suppressBackgroundClick.current) { suppressBackgroundClick.current = false; return; }
          if (event.target === event.currentTarget) setInspected(null);
        }}>
        <div className={styles.boardLabel} style={{ left: 26 }}>{mode === 'breadth' ? labels.workspace : labels.timeline}
          <small>Q{from + 1} → Q{to + 1}</small></div>
        {mode === 'breadth' ? <>
          <div className={styles.externalLabel} style={{ left: externalX }}>{labels.outside}</div>
          <svg className={styles.edges} width={boardWidth} height={boardHeight} viewBox={`0 0 ${boardWidth} ${boardHeight}`} aria-hidden="true">
            {items.filter(item => !!item.parentId).map(item => {
              const parent = itemById.get(item.parentId!);
              if (!parent) return null;
              const x1 = parent.x + parent.w;
              const y1 = parent.y + parent.h / 2;
              const x2 = item.x;
              const y2 = item.y + item.h / 2;
              const bend = Math.max(12, Math.min(44, (x2 - x1) * .45));
              const playState = playbackNodeState(item);
              return <path key={item.id} className={playState === 'current' ? styles.edgeCurrent : playState === 'failed' ? styles.edgeFailed
                : playState === 'visited' ? styles.edgeVisited : playbackIndex !== null ? styles.edgeMuted
                  : highlighted(item) && highlighted(parent) ? styles.edgeActive : styles.edgeMuted}
                d={`M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`} />;
            })}
            {externalShown.map((action, index) => {
              const origin = action.path ? items.find(item => item.kind !== 'root' && item.kind !== 'more' && item.node?.path && samePath(action.path!, item.node.path)) : undefined;
              if (!origin) return null;
              const x1 = origin.x + origin.w;
              const y1 = origin.y + origin.h / 2;
              const x2 = externalX;
              const y2 = 68 + index * 78 + 30;
              const bend = Math.max(8, Math.min(38, (x2 - x1) * .4));
              return <path key={`${action.id}-output`} className={styles.externalEdge}
                d={`M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`} />;
            })}
          </svg>
          {items.map(item => {
            if (item.kind === 'more') return <button key={item.id} type="button" className={styles.moreCard}
              style={{ left: item.x, top: item.y, width: item.w, height: item.h }}
              onClick={() => showMore(item.parentId!)}>{labels.show} +{item.hiddenCount} <span>{labels.hidden}</span></button>;
            const node = item.node;
            const folder = item.kind === 'folder';
            const isOpen = !!node && !collapsed.has(node.path) && (expanded.has(node.path) || node.touched > 0);
            const active = highlighted(item);
            const playState = playbackNodeState(item);
            const nodePlayed = node?.actions.filter(action => playedSuccessfulIds.has(action.id));
            const nodeCurrent = node?.actions.filter(action => currentActionIds.has(action.id) && successful(action));
            return <div key={item.id} className={styles.nodeWrap} style={{ left: item.x, top: item.y, width: item.w, height: item.h }}>
              <button type="button" className={`${styles.nodeCard} ${styles[item.kind]} ${active ? styles.active : styles.inactive} ${playState === 'current' ? styles.playbackCurrent : playState === 'failed' ? styles.playbackFailed : playState === 'visited' ? styles.playbackVisited : playState === 'pending' ? styles.playbackPending : ''}`}
                aria-label={`${node?.name || labels.workspace} · ${node?.touched || 0} ${labels.touch}`}
                aria-expanded={folder ? isOpen : undefined}
                onClick={() => { if (folder && node) toggleFolder(node); if (node) setInspected({ kind: 'node', node }); }}>
                <span className={styles.cardTop}>{folder || item.kind === 'root' ? <Folder size={15} weight="duotone" /> : <FileText size={15} weight="duotone" />}
                  <strong title={node?.path}>{node?.name || labels.workspace}</strong>
                  {folder && <small>{node!.children.size}{isOpen ? '⌄' : '›'}</small>}
                </span>
                {item.kind === 'file' && node && <Coverage node={node} locale={locale}
                  playedActions={playbackIndex === null ? undefined : nodePlayed} currentActions={playbackIndex === null ? undefined : nodeCurrent} />}
                <span className={styles.cardDetail}>
                  <span className={styles.detailPath}>{node?.path || labels.workspace}</span>
                  {node && <span className={styles.detailCount}>{node.touched} {labels.touch}</span>}
                  {node?.actions.slice(0, 2).map((action, index) => <span key={`${action.id}-${index}`} className={styles.detailAction}>
                    <b className={action.kind === 'write' ? styles.detailWrite : styles.detailRead}>{action.kind === 'read' ? labels.read : action.kind === 'write' ? labels.write : labels.other}</b>
                    {preview(action.section || action.detail || action.name, 72)} · {positionText(action, locale)}
                  </span>)}
                  {node && node.actions.length > 2 && <span className={styles.detailMuted}>+{node.actions.length - 2} {labels.actions}</span>}
                  {node?.unknownPositions ? <span className={styles.detailMuted}>{labels.unknown} × {node.unknownPositions}</span> : null}
                </span>
              </button>
              {item.kind === 'file' && node && onOpenFile && <button type="button" className={styles.openNodeFile}
                aria-label={`${labels.openFile}: ${node.path}`} title={labels.openFile} onClick={() => onOpenFile(node.path)}><ArrowSquareOut size={15} /></button>}
            </div>;
          })}
          {externalShown.map((action, index) => <button key={`${action.id}-${index}`} type="button"
            className={`${styles.externalCard} ${playbackIndex !== null ? currentActionIds.has(action.id) ? successful(action) ? styles.playbackCurrent : styles.playbackFailed
              : playedSuccessfulIds.has(action.id) ? styles.playbackVisited : styles.playbackPending
              : hoverActionIds ? hoverActionIds.has(action.id) ? styles.active : styles.inactive : ''}`}
            style={{ left: externalX, top: 68 + index * 78 }} onClick={() => setInspected({ kind: 'action', action })}>
            <span className={styles.cardTop}><ExternalIcon kind={action.kind} /><strong title={action.name}>{action.name}</strong></span>
            <span className={styles.externalKind}>{action.kind}{action.path ? ` · ${preview(action.path, 28)}` : ''}</span>
            <span className={styles.cardDetail}><span className={styles.detailPath}>{action.name}</span>
              {action.detail && <span className={styles.detailAction}>{preview(action.detail, 100)}</span>}
              {action.result && <span className={styles.detailAction}>{labels.result}: {preview(action.result, 110)}</span>}
            </span>
          </button>)}
          {externalActions.length > externalLimit && <button type="button" className={styles.moreCard}
            style={{ left: externalX, top: 68 + externalShown.length * 78, width: 220, height: 46 }}
            onClick={() => setExternalLimit(value => value + 32)}>{labels.show} +{externalActions.length - externalLimit}</button>}
          {!externalActions.length && <div className={styles.noOutside} style={{ left: externalX }}>{labels.noOutside}</div>}
        </> : <>
          <svg className={styles.edges} width={timelineWidth} height={timelineHeight} viewBox={`0 0 ${timelineWidth} ${timelineHeight}`} aria-hidden="true">
            {timelineSteps.slice(1).map((step, index) => {
              const previous = timelineSteps[index];
              const x1 = previous.x + 28;
              const y1 = previous.y + previous.h;
              const x2 = step.x + 28;
              const y2 = step.y;
              const failedStep = step.kind === 'action' && step.actions.some(action => !successful(action));
              return <path key={step.key} className={`${styles.timelineEdge} ${playbackIndex === index + 1 ? failedStep ? styles.edgeFailed : styles.edgeCurrent
                : playbackIndex !== null && index + 1 < playbackIndex ? failedStep ? styles.edgeFailedVisited : styles.edgeVisited : playbackIndex !== null ? styles.edgeMuted : ''}`}
                d={`M ${x1} ${y1} C ${x1} ${y1 + 28}, ${x2} ${y2 - 28}, ${x2} ${y2}`} />;
            })}
          </svg>
          {timelineSteps.map((step, index) => {
            const question = step.kind === 'question';
            const answer = step.kind === 'answer';
            const action = step.actions[0];
            const paths = [...new Set(step.actions.map(record => record.path).filter((path): path is string => !!path))];
            const openPaths = [...new Set(step.actions.map(resolveActionFilePath).filter((path): path is string => !!path))];
            // A grouped call can touch several files at once. Showing one
            // file's strip on the shared card would misrepresent the others.
            const fileNode = step.actions.length === 1 && action ? fileNodeForAction(action) : undefined;
            const text = question ? step.turn.question : answer ? step.turn.answer || labels.noAnswer
              : paths.length > 1 ? paths.join(' · ') : action?.path || action?.detail || action?.section || action?.name || '';
            const active = hoveredQuery === null || step.number - 1 === hoveredQuery;
            const failedStep = step.kind === 'action' && step.actions.some(record => !successful(record));
            return <div key={step.key} className={styles.timelineWrap} style={{ left: step.x, top: step.y, width: step.w, height: step.h }}>
              <button type="button" className={`${styles.timelineCard} ${question ? styles.timelineQuestion : answer ? styles.timelineAnswer : styles.timelineTool} ${active ? '' : styles.inactive} ${playbackIndex === index ? failedStep ? styles.playbackFailed : styles.playbackCurrent
                : playbackIndex !== null && index < playbackIndex ? failedStep ? styles.playbackFailedPast : styles.playbackVisited : playbackIndex !== null ? styles.playbackPending : ''}`}
                onClick={() => setInspected(action ? step.actions.length > 1 ? { kind: 'actionGroup', actions: step.actions } : { kind: 'action', action } : { kind: 'turn', turn: step.turn, number: step.number })}>
                <span className={styles.timelineIndex}>{String(index + 1).padStart(2, '0')}</span>
                <span className={styles.timelineContent}><span className={styles.cardTop}>
                  {action ? <ExternalIcon kind={action.kind} /> : <FileText size={15} weight="duotone" />}
                  <strong>{question ? `Q${step.number} · ${labels.questionStep}` : answer ? `Q${step.number} · ${labels.answerStep}` : action?.name}</strong>
                  {action && <small>{action.kind}{step.actions.length > 1 ? ` · ${step.actions.length}` : ''}</small>}
                </span><span className={styles.timelineSummary}>{preview(text, 160)}</span>
                {action && fileNode && <span className={styles.timelineCoverage}><Coverage node={fileNode} locale={locale}
                  playedActions={playbackIndex === null ? [action] : index <= playbackIndex ? [action] : []}
                  currentActions={playbackIndex === index ? [action] : []} /></span>}
                {action?.position && !fileNode && <small className={styles.timelinePosition}>{positionText(action, locale)}</small>}
                </span>
              </button>
              {action && onOpenFile && openPaths.length === 1 && <button type="button" className={styles.openTimelineFile}
                aria-label={`${labels.openFile}: ${openPaths[0]}`} title={labels.openFile} onClick={() => onOpenFile(openPaths[0])}><ArrowSquareOut size={17} /></button>}
            </div>;
          })}
          {totalTimelineSteps > timelineSteps.length && <button type="button" className={styles.moreTimeline}
            style={{ top: 75 + timelineSteps.length * 128, left: 176 }} onClick={() => setTimelineLimit(value => value + 120)}>
            {labels.moreSteps} +{totalTimelineSteps - timelineSteps.length}
          </button>}
        </>}
      </div>
      </div>
    </div>
    {inspected && <Inspector inspected={inspected} locale={locale} onClose={() => setInspected(null)}
      onOpenFile={onOpenFile} resolveActionFilePath={resolveActionFilePath} />}
  </div>;
}

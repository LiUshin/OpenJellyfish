import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Popover } from 'antd';
import {
  ArrowCounterClockwise, ArrowRight, GitBranch, ListBullets, Question, TreeStructure,
} from '@phosphor-icons/react';
import type { Message, MessageBlock } from '../../../types';
import type { RuntimeRun } from '../../../services/runtime';
import { readFile, type FileIndexEntry } from '../../../services/api';
import type { StreamBlock } from '../types';
import {
  extractTraceCoverage, extractTracePosition, mapTraceCoverage,
  type TraceCoverageEvidence, type TracePosition,
} from '../utils/tracePosition';
import TraceCanvas from './TraceCanvas';
import styles from './tracingView.module.css';

type ActionKind = 'read' | 'write' | 'browse' | 'search' | 'command' | 'external' | 'other' | 'output';
type TraceAction = {
  id: string;
  callId: string;
  name: string;
  kind: ActionKind;
  path?: string;
  detail?: string;
  result?: string;
  section?: string;
  position?: TracePosition;
  coverage?: TraceCoverageEvidence;
  input?: string;
  output?: string;
  diff?: string;
  oldText?: string;
  newText?: string;
  complete: boolean;
  succeeded: boolean;
};
type TraceTurn = { id: string; question: string; answer: string; actions: TraceAction[]; timestamp?: number };
type FileNode = {
  name: string;
  path: string;
  isDir: boolean;
  touched: number;
  children: Map<string, FileNode>;
  actions: TraceAction[];
  positionCells: ('read' | 'write' | 'both' | 'empty')[];
  readCells?: boolean[];
  addedCells?: boolean[];
  deletedCells?: boolean[];
  totalLines?: number;
  totalBytes?: number;
  unknownRead?: number;
  unknownWrite?: number;
  unknownPositions?: number;
  positionApproximate?: boolean;
};
const EMPTY_WORKSPACE_FILES: FileIndexEntry[] = [];

const pathKeys = new Set(['path', 'file_path', 'filepath', 'filename', 'file', 'files', 'paths', 'document_path', 'doc_path', 'workspace_path', 'uri']);
const sectionKeys = ['section', 'heading', 'start_line', 'end_line', 'line_range', 'offset', 'limit'];

function parseArgs(raw: string): unknown {
  try { return JSON.parse(raw); } catch { return raw; }
}

function flattenValues(value: unknown): string[] {
  if (typeof value === 'string') return [value];
  if (Array.isArray(value)) return value.flatMap(flattenValues);
  return [];
}

function normalizePath(raw: string): string | undefined {
  const value = raw.trim().replace(/^['"`]|['"`]$/g, '');
  if (!value || /^https?:\/\//i.test(value) || value.length > 300) return undefined;
  if (/\n|\r|\{|\}/.test(value)) return undefined;
  // Only explicit file-like values enter the tree. This avoids treating free text as a document.
  if (!/[\\/]/.test(value) && !/\.[a-z0-9]{1,8}$/i.test(value)) return undefined;
  return value.replace(/\\/g, '/').replace(/\/{2,}/g, '/');
}

function pathsFromArgs(parsed: unknown): string[] {
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return [];
  const record = parsed as Record<string, unknown>;
  const values = Object.entries(record).flatMap(([key, value]) => {
    const normalizedKey = key.toLowerCase();
    if (pathKeys.has(normalizedKey)) return flattenValues(value);
    if (['input', 'params', 'arguments'].includes(normalizedKey)) return pathsFromArgs(value);
    return [];
  });
  return [...new Set(values.map(normalizePath).filter((value): value is string => !!value))];
}

function sectionFromArgs(parsed: unknown): string | undefined {
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return undefined;
  const record = parsed as Record<string, unknown>;
  const items = sectionKeys.flatMap(key => {
    const value = record[key];
    return typeof value === 'string' || typeof value === 'number' ? [`${key}: ${String(value).slice(0, 70)}`] : [];
  });
  return items.length ? items.join(' · ') : undefined;
}

function classify(name: string): ActionKind {
  const tool = name.toLowerCase();
  if (/write|edit|patch|delete|move_file|rename_file|create_file|save_document|filechange|^file_change$/.test(tool)) return 'write';
  if (/read_file|read_document|read_service_document|view_file|open_file|read_page|fetch_file|^read$|^file_read$/.test(tool)) return 'read';
  if (/list_dir|list_files|glob|directory|tree/.test(tool)) return 'browse';
  if (/web_search|websearch|tavily|search_web|internet_search|网页搜索|搜索网页|^搜索$|^search$/.test(tool)) return 'search';
  if (/exec|command|shell|terminal|python|script|run_code/.test(tool)) return 'command';
  if (/fetch_url|browser|http|image_generation|imagegen|生成图片|读取网页|search/.test(tool)) return 'external';
  return 'other';
}

function shortDetail(parsed: unknown): string | undefined {
  if (typeof parsed === 'string') return parsed.replace(/\s+/g, ' ').slice(0, 130) || undefined;
  if (!parsed || typeof parsed !== 'object') return undefined;
  const record = parsed as Record<string, unknown>;
  const field = ['query', 'search_query', 'command', 'cmd', 'url', 'prompt'].find(key => typeof record[key] === 'string');
  return field ? String(record[field]).replace(/\s+/g, ' ').slice(0, 130) : undefined;
}

/** Keep inspection useful without copying unbounded tool output into every node. */
function evidenceText(value: string | undefined, limit = 24000): string | undefined {
  if (!value) return undefined;
  return value.length > limit ? `${value.slice(0, limit)}\n… (+${value.length - limit})` : value;
}

function actionsFromBlocks(blocks: (MessageBlock | StreamBlock)[] | undefined, fallback: Message['tool_calls'] = []): TraceAction[] {
  const tools = blocks?.filter((block): block is Extract<MessageBlock | StreamBlock, { type: 'tool' }> => block.type === 'tool') || [];
  const source = tools.length ? tools : fallback.map(call => ({ type: 'tool' as const, ...call, done: true }));
  const actions: TraceAction[] = [];
  source.forEach((tool, index) => {
    const parsed = parseArgs(tool.args || '');
    const changes = 'changes' in tool && Array.isArray(tool.changes) ? tool.changes : [];
    const changedPaths = changes.map(change => normalizePath(change.path)).filter((path): path is string => !!path);
    const kind = classify(tool.name);
    // File-change records identify the actual write targets. Tool arguments may
    // carry the same targets in a different absolute/relative spelling.
    const explicitPaths = [...new Set(kind === 'write' && changedPaths.length
      ? changedPaths : [...pathsFromArgs(parsed), ...changedPaths])];
    const paths = explicitPaths.length ? explicitPaths
      : typeof parsed === 'string' && ['read', 'write', 'browse'].includes(classify(tool.name))
        ? [normalizePath(parsed)].filter((path): path is string => !!path) : [];
    const section = sectionFromArgs(parsed);
    const detail = shortDetail(parsed);
    const result = typeof tool.result === 'string' ? cleanPreview(tool.result, 190) : undefined;
    const input = evidenceText(tool.args);
    const output = evidenceText(tool.result);
    const complete = tool.done !== false;
    const status = 'status' in tool ? String(tool.status || '') : '';
    const exitCode = 'exit_code' in tool ? tool.exit_code : null;
    const failedResult = typeof tool.result === 'string' && /^\s*(?:error[:\s]|failed[:\s]|traceback\b|错误[:：]|失败[:：])/i.test(tool.result);
    const succeeded = complete && !['failed', 'cancelled', 'declined', 'unknown'].includes(status)
      && (exitCode == null || exitCode === 0) && !failedResult;
    // A tool invocation can touch several explicit paths. Keep a node for each one.
    if (paths.length) paths.forEach((path, pathIndex) => {
      const pathChanges = changes.filter(change => normalizePath(change.path) === path);
      const diffs = pathChanges.map(change => change.diff).filter((value): value is string => !!value);
      const oldTexts = pathChanges.map(change => change.old_text).filter((value): value is string => typeof value === 'string');
      const newTexts = pathChanges.map(change => change.new_text).filter((value): value is string => typeof value === 'string');
      actions.push({
        id: `${index}-${pathIndex}`, callId: String(index), name: tool.name, kind, path, section, detail, result,
        position: extractTracePosition(tool.name, tool.args || '', pathChanges),
        coverage: extractTraceCoverage(tool.name, tool.args || '', pathChanges),
        input, output, diff: evidenceText(diffs.join('\n\n')), oldText: evidenceText(oldTexts.join('\n\n')),
        newText: evidenceText(newTexts.join('\n\n')),
        complete, succeeded,
      });
    });
    else actions.push({ id: `${index}`, callId: String(index), name: tool.name, kind, section, detail, result,
      input, output, complete, succeeded });
  });
  return actions;
}

function answerFromBlocks(blocks: MessageBlock[] | StreamBlock[] | undefined): string {
  return blocks?.filter((block): block is Extract<MessageBlock | StreamBlock, { type: 'text' }> => block.type === 'text')
    .map(block => block.content).join(' ').trim() || '';
}

function cleanPreview(value: string, max = 130): string {
  const text = value.replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function turnsFromMessages(messages: Message[], onlyServiceTest = false): TraceTurn[] {
  const turns: TraceTurn[] = [];
  let current: TraceTurn | undefined;
  let assistantOrdinal = 0;
  for (const message of messages) {
    if (message.role === 'user') {
      assistantOrdinal = 0;
      current = onlyServiceTest && !message.test_service_id ? undefined : {
        id: `message-${turns.length}`, question: message.content.trim(), answer: '', actions: [],
        timestamp: message.timestamp ? Date.parse(message.timestamp) : undefined,
      };
      if (current) turns.push(current);
    } else if (message.role === 'assistant' && current) {
      const turn = current;
      const answer = message.content.trim() || answerFromBlocks(message.blocks);
      if (answer) turn.answer = [turn.answer, answer].filter(Boolean).join('\n');
      const actions = actionsFromBlocks(message.blocks, message.tool_calls);
      turn.actions.push(...actions.map((action, index) => ({ ...action, id: `${turn.id}-${turn.actions.length + index}`, callId: `${turn.id}-${assistantOrdinal}-${action.callId}` })));
      assistantOrdinal++;
    }
  }
  return turns;
}

function turnsFromRuns(runs: RuntimeRun[]): TraceTurn[] {
  return [...runs].sort((left, right) => left.created_at - right.created_at || left.seq - right.seq).map(run => ({
    id: run.id,
    question: run.message,
    answer: run.output || answerFromBlocks(run.blocks),
    timestamp: run.created_at * 1000,
    actions: [
      ...actionsFromBlocks(run.blocks).map((action, actionIndex) => ({ ...action, id: `${run.id}-${actionIndex}`, callId: `${run.id}-${action.callId}` })),
      ...run.artifacts.map((artifact, artifactIndex): TraceAction => ({
        id: `${run.id}-artifact-${artifactIndex}`, callId: `${run.id}-artifact-${artifactIndex}`, name: 'artifact', kind: 'output',
        path: artifact.path || artifact.name, complete: true, succeeded: true,
      })),
    ],
  }));
}

function makeTree(actions: TraceAction[], workspaceFiles: FileIndexEntry[]): FileNode[] {
  const root = new Map<string, FileNode>();
  const indexedPaths = new Set(workspaceFiles.map(entry => entry.path.replace(/\/{2,}/g, '/')));
  const indexedSuffixes = [...indexedPaths].sort((left, right) => right.length - left.length);
  const addPath = (rawPath: string, isDir: boolean, action?: TraceAction) => {
    const normalized = rawPath.replace(/^\.\//, '');
    const parts = normalized.split('/').filter(Boolean);
    if (!parts.length) return;
    let branch = root;
    let current = normalized.startsWith('/') ? '/' : '';
    for (const [index, part] of parts.entries()) {
      current += (current && current !== '/' ? '/' : '') + part;
      let node = branch.get(part);
      if (!node) {
        node = { name: part, path: current, isDir: true, touched: 0, children: new Map(), actions: [], positionCells: Array(10).fill('empty') };
        branch.set(part, node);
      }
      if (index === parts.length - 1) node.isDir = isDir;
      if (action) {
        if (action.succeeded) node.touched++;
        if (index === parts.length - 1) node.actions.push(action);
      }
      branch = node.children;
    }
  };
  for (const entry of workspaceFiles) addPath(entry.path, entry.is_dir);
  for (const action of actions) {
    if (!action.path || !['read', 'write', 'browse', 'output'].includes(action.kind)) continue;
    const normalized = action.path.replace(/^\.\//, '').replace(/\/{2,}/g, '/');
    const candidate = normalized.startsWith('/') ? normalized : `/${normalized}`;
    // CLI tools sometimes report an absolute host path while the workspace index
    // reports a workspace-relative path. Match only an exact suffix from the index.
    const canonical = indexedPaths.has(normalized) ? normalized : indexedPaths.has(candidate) ? candidate
      : indexedSuffixes.find(path => normalized === path || normalized.endsWith(`/${path.replace(/^\/+/, '')}`)) || normalized;
    addPath(canonical, action.kind === 'browse', action);
  }
  return [...root.values()];
}

function countTouchedPaths(nodes: FileNode[]): number {
  return nodes.reduce((sum, node) => sum + (node.actions.some(action => action.succeeded) ? 1 : 0)
    + countTouchedPaths([...node.children.values()]), 0);
}

function isExternal(action: TraceAction): boolean {
  return ['search', 'command', 'external', 'other'].includes(action.kind);
}

type Labels = typeof zh;
const zh = {
  subtitle: '根据可见的消息和工具记录重建动线。',
  query: '轮提问', tools: '次工具调用', files: '处路径触达', external: '次外部动作',
  range: '查看范围', from: '从', to: '到', breadth: '工作区关系', timeline: '执行顺序',
  overview: '问题与触达', workspace: '工作区关系画布', outside: '其他工具与外部信息',
  noWorkspace: '选定范围内没有可识别的文件路径。', noExternal: '没有记录到外部信息或脚本操作。',
  noActions: '这一轮没有可见工具记录。', noTurns: '当前对话还没有可追踪的提问。',
  viewQuery: '只看此轮', read: '读取', write: '写入', browse: '浏览', search: '搜索', command: '执行', externalAction: '外部', other: '工具', output: '产物',
  stepQuestion: '用户提问', stepAnswer: '回复', stepDecision: '可见选择',
  decisionNote: '下方仅展示记录到的工具选择，无法还原模型内部思考。', choseFirst: '首先选择', thenMore: '，随后还有', moreActions: '次工具动作。', period: '。',
  answerPending: '尚无已记录的回复',
  bridgeTitle: '跨轮上下文', bridgeDetail: '相邻轮次可以在这里对照；实际传入下一轮模型的历史片段和裁剪结果未记录，无法确认哪些内容进入了上下文。',
  priorAnswer: '上一轮记录的回复', nextQuery: '下一轮提问', contextUnknown: '实际进入模型的上下文未知', result: '返回内容',
  precisionTitle: '覆盖口径', precisionDetail: '十格按记录中的行号或 offset 映射到当前文件长度；历史版本长度未记录，因此位置仅作大致参考。无位置记录的访问保持灰色。',
  indexVisible: '索引可见文件', indexUnavailable: '工作区索引未加载，仅显示记录到的路径。', indexTruncated: '工作区索引已截断，灰色区域仅代表索引可见范围。', indexPartial: '灰色文件是当前索引中未记录触达的区域；隐藏文件等可能不在索引中。',
  collapse: '收起', expand: '展开', more: '展开其余', noQuestion: '未命名提问', toolWithoutTarget: '目标未记录',
};
const en: Labels = {
  subtitle: 'Reconstructed from visible messages and tool records.',
  query: 'queries', tools: 'tool calls', files: 'paths touched', external: 'external actions',
  range: 'Range', from: 'From', to: 'To', breadth: 'Workspace map', timeline: 'Execution order',
  overview: 'Queries and touches', workspace: 'Workspace canvas', outside: 'Other tools and external information',
  noWorkspace: 'No explicit file paths were recorded in this range.', noExternal: 'No external or script actions were recorded.',
  noActions: 'No visible tool records for this query.', noTurns: 'This conversation has no queries to trace yet.',
  viewQuery: 'View this query', read: 'Read', write: 'Write', browse: 'Browse', search: 'Search', command: 'Run', externalAction: 'External', other: 'Tool', output: 'Output',
  stepQuestion: 'User query', stepAnswer: 'Response', stepDecision: 'Visible choice',
  decisionNote: 'Only recorded tool choices are shown below; internal reasoning cannot be reconstructed.', choseFirst: 'First chose to', thenMore: ', followed by', moreActions: 'more tool actions.', period: '.',
  answerPending: 'No recorded response yet',
  bridgeTitle: 'Across queries', bridgeDetail: 'Adjacent queries can be compared here. The exact history and truncation supplied to the next model call were not recorded.',
  priorAnswer: 'Recorded prior answer', nextQuery: 'Next query', contextUnknown: 'Actual model context unknown', result: 'Returned content',
  precisionTitle: 'What this map covers', precisionDetail: 'The ten cells map recorded lines or offsets to the current file length. Historical file length was not recorded, so locations are approximate. Unknown positions remain grey.',
  indexVisible: 'indexed files', indexUnavailable: 'Workspace index unavailable; only recorded paths are shown.', indexTruncated: 'Workspace index is truncated; grey areas represent only the indexed portion.', indexPartial: 'Grey files have no recorded touch in this range. Hidden files and other paths may be absent from the index.',
  collapse: 'Collapse', expand: 'Expand', more: 'Show more', noQuestion: 'Untitled query', toolWithoutTarget: 'Target not recorded',
};

export default function TracingView({ messages, runtimeRuns, workspaceFiles = EMPTY_WORKSPACE_FILES, workspaceIndexLoaded = false, workspaceIndexTruncated = false, onOpenFile, traceTitle, onRefresh, fullscreen = false, onToggleFullscreen }: {
  messages: Message[];
  runtimeRuns?: RuntimeRun[];
  workspaceFiles?: FileIndexEntry[];
  workspaceIndexLoaded?: boolean;
  workspaceIndexTruncated?: boolean;
  onOpenFile?: (path: string) => void;
  traceTitle?: string;
  onRefresh?: () => void;
  fullscreen?: boolean;
  onToggleFullscreen?: () => void;
}) {
  const { i18n } = useTranslation();
  const labels = i18n.language.startsWith('zh') ? zh : en;
  const turns = useMemo(() => runtimeRuns
    ? [...turnsFromRuns(runtimeRuns), ...turnsFromMessages(messages, true)]
      .sort((left, right) => (left.timestamp ?? Number.MAX_SAFE_INTEGER) - (right.timestamp ?? Number.MAX_SAFE_INTEGER))
    : turnsFromMessages(messages), [messages, runtimeRuns]);
  const [from, setFrom] = useState(0);
  const [to, setTo] = useState(Number.MAX_SAFE_INTEGER);
  const [mode, setMode] = useState<'breadth' | 'timeline'>('breadth');
  const [lineTotals, setLineTotals] = useState<Map<string, number>>(new Map());
  const attemptedLineTotals = useRef<Set<string>>(new Set());
  const first = Math.min(from, Math.max(turns.length - 1, 0));
  const last = Math.min(Math.max(to, first), Math.max(turns.length - 1, 0));
  const selected = turns.slice(first, last + 1);
  const actions = selected.flatMap(turn => turn.actions);
  const baseTree = useMemo(() => makeTree(actions, workspaceFiles), [turns, first, last, workspaceFiles]);
  const indexedFiles = useMemo(() => new Map(workspaceFiles.filter(entry => !entry.is_dir).map(entry => [entry.path.replace(/\/{2,}/g, '/'), entry])), [workspaceFiles]);
  useEffect(() => {
    const candidates: string[] = [];
    const visit = (nodes: FileNode[]) => nodes.forEach(node => {
      const entry = indexedFiles.get(node.path);
      if (entry && entry.size <= 512 * 1024 && node.actions.some(action => action.succeeded && (
        (action.position?.unit === 'line' && !action.position.wholeFile)
        || [action.coverage?.reads, action.coverage?.added, action.coverage?.deleted]
          .some(positions => positions?.some(position => position.unit === 'line' && !position.wholeFile))
      ))
        && !attemptedLineTotals.current.has(node.path)) {
        candidates.push(node.path);
      }
      visit([...node.children.values()]);
    });
    visit(baseTree);
    // Current file content is used only as a denominator for approximate
    // position buckets. Keep the lookup bounded for large workspaces.
    const targets = candidates.slice(0, 24);
    if (!targets.length) return;
    targets.forEach(path => attemptedLineTotals.current.add(path));
    let live = true;
    void Promise.allSettled(targets.map(async path => {
      const { content } = await readFile(path);
      if (content === '[二进制文件，无法预览]') return [path, 0] as const;
      const parts = content.split(/\r\n|\n|\r/);
      const lines = content ? parts.length - (/\r\n$|\n$|\r$/.test(content) ? 1 : 0) : 0;
      return [path, lines] as const;
    })).then(results => {
      if (!live) return;
      const found = results.filter((result): result is PromiseFulfilledResult<readonly [string, number]> => result.status === 'fulfilled' && !!result.value);
      if (found.length) setLineTotals(previous => {
        const next = new Map(previous);
        found.forEach(result => next.set(result.value[0], result.value[1]));
        return next;
      });
    });
    return () => {
      live = false;
      targets.forEach(path => attemptedLineTotals.current.delete(path));
    };
  }, [baseTree, indexedFiles]);
  const tree = useMemo(() => {
    const decorate = (node: FileNode): FileNode => {
      const entry = indexedFiles.get(node.path);
      const coverage = mapTraceCoverage(
        node.actions.filter(action => action.succeeded && !!action.coverage)
          .map(action => action.coverage!),
        { lines: lineTotals.get(node.path), bytes: entry?.size },
      );
      return { ...node, readCells: coverage.readCells, addedCells: coverage.addedCells,
        deletedCells: coverage.deletedCells, totalLines: lineTotals.get(node.path), totalBytes: entry?.size,
        unknownRead: coverage.unknownRead, unknownWrite: coverage.unknownWrite,
        unknownPositions: coverage.unknownRead + coverage.unknownWrite,
        positionApproximate: coverage.approximate,
        children: new Map([...node.children].map(([key, child]) => [key, decorate(child)])) };
    };
    return baseTree.map(decorate);
  }, [baseTree, indexedFiles, lineTotals]);
  const external = actions.filter(isExternal);
  const toolCount = new Set(actions.filter(action => action.kind !== 'output').map(action => action.callId)).size;

  if (!turns.length) return <div className={styles.empty}><GitBranch size={36} weight="duotone" /><strong>{labels.noTurns}</strong><span>{labels.subtitle}</span></div>;

  const coverageNote = !workspaceIndexLoaded ? labels.indexUnavailable : workspaceIndexTruncated ? labels.indexTruncated : labels.indexPartial;

  return <div className={`${styles.root} ${fullscreen ? styles.rootFullscreen : ''}`}>
    <div className={styles.overviewRow}>
      <div className={styles.overviewTitle}>
        <strong>{traceTitle || (i18n.language.startsWith('zh') ? 'Tracing · 对话轨迹' : 'Tracing · conversation path')}</strong>
        <Popover trigger={['hover', 'focus']} placement="bottomLeft" content={<div className={styles.helpContent}>
          <strong>{labels.bridgeTitle}</strong><span>{labels.bridgeDetail}</span>
          <strong>{labels.precisionTitle}</strong><span>{labels.precisionDetail} {coverageNote}</span>
        </div>}>
          <button type="button" className={styles.helpButton}
            aria-label={`${labels.bridgeTitle} · ${labels.precisionTitle}`}><Question size={13} weight="bold" /></button>
        </Popover>
      </div>
      <div className={styles.metrics} aria-label={labels.subtitle}>
        <div title={`${selected.length} ${labels.query}`}><strong>{selected.length}</strong><span>{labels.query}</span></div>
        <div title={`${toolCount} ${labels.tools}`}><strong>{toolCount}</strong><span>{labels.tools}</span></div>
        <div title={`${countTouchedPaths(baseTree)} ${labels.files}`}><strong>{countTouchedPaths(baseTree)}</strong><span>{labels.files}</span></div>
        <div title={`${external.length} ${labels.external}`}><strong>{external.length}</strong><span>{labels.external}</span></div>
      </div>
    </div>

    <div className={styles.toolbar}>
      <button type="button" className={styles.refreshButton} onClick={onRefresh} disabled={!onRefresh}
        aria-label={i18n.language.startsWith('zh') ? '刷新轨迹' : 'Refresh trace'}
        title={i18n.language.startsWith('zh') ? '刷新轨迹' : 'Refresh trace'}>
        <ArrowCounterClockwise size={17} />
      </button>
      <div className={styles.range}><span>{labels.range}</span>
        <label>{labels.from}<select aria-label={labels.from} value={first} onChange={event => {
          const value = Number(event.target.value); setFrom(value); if (value > last) setTo(value);
        }}>{turns.map((_, index) => <option key={index} value={index}>Q{index + 1}</option>)}</select></label>
        <ArrowRight size={14} />
        <label>{labels.to}<select aria-label={labels.to} value={last} onChange={event => {
          const value = Number(event.target.value); setTo(value); if (value < first) setFrom(value);
        }}>{turns.map((_, index) => <option key={index} value={index}>Q{index + 1}</option>)}</select></label>
      </div>
      <div className={styles.modeSwitch} role="group" aria-label={labels.range}>
        <button type="button" className={mode === 'breadth' ? styles.modeActive : ''} aria-pressed={mode === 'breadth'} onClick={() => setMode('breadth')}><TreeStructure size={17} />{labels.breadth}</button>
        <button type="button" className={mode === 'timeline' ? styles.modeActive : ''} aria-pressed={mode === 'timeline'} onClick={() => setMode('timeline')}><ListBullets size={17} />{labels.timeline}</button>
      </div>
    </div>

    <div className={styles.canvasSlot}><TraceCanvas
      nodes={tree}
      turns={turns}
      from={first}
      to={last}
      mode={mode}
      onRangeChange={(nextFrom, nextTo) => { setFrom(nextFrom); setTo(nextTo); }}
      onOpenFile={onOpenFile}
      fullscreen={fullscreen}
      onToggleFullscreen={onToggleFullscreen}
      externalActions={external}
      locale={i18n.language.startsWith('zh') ? 'zh' : 'en'}
    /></div>
  </div>;
}

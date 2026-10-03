/** A half-open range in a file. Line offsets are zero-based; end is exclusive. */
export interface TracePosition {
  unit: 'line' | 'byte';
  start: number;
  end: number;
  wholeFile?: boolean;
  /** A deletion is located at a boundary in the resulting file. */
  boundary?: boolean;
  source: string;
}

type ToolChange = { diff?: string; path?: string };
type Access = { kind: 'read' | 'write'; position?: TracePosition };
type Segment = 'read' | 'write' | 'both' | 'empty';

function argsObject(raw: string): Record<string, unknown> | undefined {
  try {
    let value: unknown = JSON.parse(raw);
    // Some providers wrap the actual tool arguments in one of these envelopes.
    for (let depth = 0; depth < 3; depth++) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
      const record = value as Record<string, unknown>;
      const nested = record.input ?? record.params ?? record.arguments;
      if (nested && typeof nested === 'object' && !Array.isArray(nested)) value = nested;
      else return record;
    }
    return value as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

function integer(value: unknown, minimum: number): number | undefined {
  const numeric = typeof value === 'string' && /^\d+$/.test(value.trim()) ? Number(value.trim()) : value;
  return typeof numeric === 'number' && Number.isSafeInteger(numeric) && numeric >= minimum ? numeric : undefined;
}

function explicitLines(args: Record<string, unknown>): TracePosition | undefined {
  const range = args.line_range;
  let start: number | undefined;
  let end: number | undefined;
  if (Array.isArray(range) && range.length >= 2) {
    start = integer(range[0], 1);
    end = integer(range[1], 1);
  } else if (typeof range === 'string') {
    const match = /^\s*(?:L|line\s*)?(\d+)\s*[-–—:,]\s*(?:L|line\s*)?(\d+)\s*$/i.exec(range);
    if (match) {
      start = integer(match[1], 1);
      end = integer(match[2], 1);
    }
  } else if (range && typeof range === 'object') {
    const fields = range as Record<string, unknown>;
    start = integer(fields.start ?? fields.start_line, 1);
    end = integer(fields.end ?? fields.end_line, 1);
  }
  if (start !== undefined && end !== undefined && end >= start) {
    return { unit: 'line', start: start - 1, end, source: 'line_range (1-based inclusive)' };
  }

  start = integer(args.start_line, 1);
  end = integer(args.end_line, 1);
  if (start !== undefined && (end === undefined || end >= start)) {
    return { unit: 'line', start: start - 1, end: end ?? start, source: 'start_line/end_line (1-based inclusive)' };
  }
  return undefined;
}

function explicitBytes(args: Record<string, unknown>): TracePosition | undefined {
  // Generic `offset` can mean lines, bytes, characters or pagination. Only
  // keys with an explicit byte unit are safe to project onto the file size.
  const start = integer(args.byte_offset ?? args.start_byte, 0);
  const length = integer(args.byte_limit ?? args.byte_length, 1);
  const end = integer(args.end_byte, 1);
  if (start === undefined || (length === undefined && (end === undefined || end <= start))) return undefined;
  return { unit: 'byte', start, end: length !== undefined ? start + length : end!, source: 'explicit byte range' };
}

function diffHunk(changes: ToolChange[] | undefined): TracePosition | undefined {
  if (!changes?.length) return undefined;
  const hunks = changes.flatMap(change => {
    const diff = change.diff || '';
    return [...diff.matchAll(/^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@/gm)];
  });
  // One broad interval over separated hunks would falsely mark untouched lines.
  if (hunks.length !== 1) return undefined;
  const line = Number(hunks[0][1]);
  const count = hunks[0][2] === undefined ? 1 : Number(hunks[0][2]);
  if (!Number.isSafeInteger(line) || !Number.isSafeInteger(count) || line < 0 || count < 0) return undefined;
  const start = Math.max(0, line - 1);
  // A deletion has no new-side lines. Mark its boundary as an approximate point.
  return { unit: 'line', start, end: start + Math.max(1, count), source: 'unified diff hunk (new side)' };
}

/**
 * Locate only positions established by a file tool's recorded arguments/diff.
 * `read_file.offset` is a zero-based line offset, whereas explicit `*_line`
 * fields are treated as one-based inclusive line numbers. Generic `offset`
 * values on other tools may mean pagination or characters, so are ignored.
 */
export function extractTracePosition(toolName: string, rawArgs: string, changes?: ToolChange[]): TracePosition | undefined {
  const name = toolName.toLowerCase().trim();
  if (/(^|[\s_:])write_file$/.test(name)) {
    return { unit: 'line', start: 0, end: 0, wholeFile: true, source: 'whole-file write' };
  }

  const fromDiff = diffHunk(changes);
  if (fromDiff && /edit|patch|change|write/.test(name)) return fromDiff;

  const args = argsObject(rawArgs);
  if (!args) return undefined;
  const fromLines = explicitLines(args);
  if (fromLines) return fromLines;

  const fromBytes = explicitBytes(args);
  if (fromBytes) return fromBytes;

  if (/(^|[\s_:])read_file$/.test(name)) {
    const offset = integer(args.offset, 0) ?? 0;
    const limit = integer(args.limit, 1) ?? 2000;
    return { unit: 'line', start: offset, end: offset + limit, source: 'read_file offset/limit (0-based lines)' };
  }
  return undefined;
}

/** Project known ranges onto ten equal parts of the supplied current file size. */
export function mapPositionSegments(accesses: Access[], totals: { lines?: number; bytes?: number }): {
  cells: Segment[];
  unknown: number;
  approximate: boolean;
} {
  const cells: Segment[] = Array(10).fill('empty');
  let unknown = 0;
  let approximate = false;
  for (const { kind, position } of accesses) {
    if (!position) { unknown++; continue; }
    const total = position.unit === 'line' ? totals.lines : totals.bytes;
    if (!position.wholeFile && (typeof total !== 'number' || !Number.isFinite(total) || total <= 0 || position.end <= position.start)) {
      unknown++;
      continue;
    }
    if (!position.wholeFile && position.start >= total!) {
      // The file may have changed since this historic action was recorded.
      unknown++;
      continue;
    }
    approximate = true; // Ten buckets and current-file totals are approximate.
    for (let index = 0; index < 10; index++) {
      const cellStart = position.wholeFile ? 0 : (index * total!) / 10;
      const cellEnd = position.wholeFile ? 1 : ((index + 1) * total!) / 10;
      if (!position.wholeFile && (position.start >= cellEnd || position.end <= cellStart)) continue;
      const previous = cells[index];
      cells[index] = previous === 'empty' || previous === kind ? kind : 'both';
    }
  }
  return { cells, unknown, approximate };
}

/** Separate visual layers for what a recorded tool read, added and removed. */
export interface TraceCoverageEvidence {
  reads: TracePosition[];
  added: TracePosition[];
  deleted: TracePosition[];
  unknownRead: boolean;
  unknownWrite: boolean;
}

export interface TraceCoverageMap {
  readCells: boolean[];
  addedCells: boolean[];
  deletedCells: boolean[];
  unknownRead: number;
  unknownWrite: number;
  approximate: boolean;
}

const emptyCoverage = (): TraceCoverageEvidence => ({
  reads: [], added: [], deleted: [], unknownRead: false, unknownWrite: false,
});

function diffRanges(diff: string): Pick<TraceCoverageEvidence, 'added' | 'deleted' | 'unknownWrite'> {
  const added: TracePosition[] = [];
  const deleted: TracePosition[] = [];
  let unknownWrite = false;
  let sawHunk = false;
  let inHunk = false;
  let oldLine = 0;
  let newLine = 0;
  let hunkChanged = false;

  const closeHunk = () => {
    if (inHunk && !hunkChanged) unknownWrite = true;
  };
  for (const line of diff.split(/\r?\n/)) {
    const header = /^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@/.exec(line);
    if (header) {
      closeHunk();
      sawHunk = true;
      inHunk = true;
      hunkChanged = false;
      oldLine = Number(header[1]);
      newLine = Number(header[3]);
      if (!Number.isSafeInteger(oldLine) || !Number.isSafeInteger(newLine)) {
        inHunk = false;
        unknownWrite = true;
      }
      continue;
    }
    if (!inHunk) continue;
    if (line.startsWith('diff --git ')) {
      closeHunk();
      inHunk = false;
      continue;
    }
    if (line.startsWith('\\ No newline at end of file')) continue;
    if (line.startsWith(' ')) {
      oldLine++;
      newLine++;
    } else if (line.startsWith('+')) {
      hunkChanged = true;
      const start = Math.max(0, newLine - 1);
      const previous = added[added.length - 1];
      if (previous && previous.end === start) previous.end++;
      else added.push({ unit: 'line', start, end: start + 1, source: 'unified diff addition (new side)' });
      newLine++;
    } else if (line.startsWith('-')) {
      hunkChanged = true;
      // Deleted text has no line in the resulting file. Its insertion boundary
      // is the only position that can be projected onto the current file.
      const start = Math.max(0, newLine - 1);
      const previous = deleted[deleted.length - 1];
      if (!previous || previous.start !== start) deleted.push({
        unit: 'line', start, end: start + 1, boundary: true,
        source: 'unified diff deletion boundary (new side)',
      });
      oldLine++;
    } else if (line !== '') {
      // The record contains a hunk with unrecognized body syntax.
      unknownWrite = true;
    }
  }
  closeHunk();
  if (!sawHunk) unknownWrite = true;
  return { added, deleted, unknownWrite };
}

/**
 * Extract only locations supported by recorded arguments or unified diff.
 * Additions use resulting-file line positions. Deletions use resulting-file
 * boundaries, which are necessarily approximate when viewed later.
 */
export function extractTraceCoverage(toolName: string, rawArgs: string, changes?: ToolChange[]): TraceCoverageEvidence {
  const evidence = emptyCoverage();
  const name = toolName.toLowerCase().trim();
  const readTool = /(^|[\s_:])read_file$/.test(name) || /read_document|read_service_document|view_file|open_file|read_page|fetch_file|^read$|^file_read$/.test(name);
  const writeTool = /write|edit|patch|delete|create_file|save_document|filechange|file_change|move_file|rename_file/.test(name);
  const args = argsObject(rawArgs);

  if (readTool) {
    const position = extractTracePosition(toolName, rawArgs);
    if (position) evidence.reads.push(position);
    else evidence.unknownRead = true;
  }

  if (!writeTool && !changes?.some(change => change.diff)) return evidence;

  const diffs = changes?.map(change => change.diff).filter((diff): diff is string => !!diff) || [];
  if (diffs.length) {
    for (const diff of diffs) {
      const parsed = diffRanges(diff);
      evidence.added.push(...parsed.added);
      evidence.deleted.push(...parsed.deleted);
      evidence.unknownWrite ||= parsed.unknownWrite;
    }
    return evidence;
  }

  const explicit = args ? explicitLines(args) ?? explicitBytes(args) : undefined;
  if (explicit) {
    const deletion = /(^|[\s_:])delete_file$/.test(name);
    const range = { ...explicit, source: `${explicit.source}; ${deletion ? 'delete' : 'write'} extent` };
    if (deletion) evidence.deleted.push(range);
    else evidence.added.push(range);
    return evidence;
  }

  if (/(^|[\s_:])(?:write_file|create_file)$/.test(name)) {
    // A successful whole-file write establishes the new content, but without
    // an old version it says nothing about which old lines were removed.
    evidence.added.push({ unit: 'line', start: 0, end: 0, wholeFile: true, source: 'whole-file write (old content unknown)' });
    return evidence;
  }
  if (/(^|[\s_:])delete_file$/.test(name)) {
    evidence.deleted.push({ unit: 'line', start: 0, end: 0, wholeFile: true, source: 'whole-file delete' });
    return evidence;
  }
  evidence.unknownWrite = true;
  return evidence;
}

/** Project each layer independently; unknown positions never fill leading cells. */
export function mapTraceCoverage(evidence: TraceCoverageEvidence[], totals: { lines?: number; bytes?: number }): TraceCoverageMap {
  const mapped: TraceCoverageMap = {
    readCells: Array(10).fill(false), addedCells: Array(10).fill(false), deletedCells: Array(10).fill(false),
    unknownRead: 0, unknownWrite: 0, approximate: false,
  };
  const mark = (position: TracePosition, cells: boolean[], kind: 'read' | 'write') => {
    const total = position.unit === 'line' ? totals.lines : totals.bytes;
    if (!position.wholeFile && (typeof total !== 'number' || !Number.isFinite(total) || total <= 0 || position.end <= position.start)) {
      if (kind === 'read') mapped.unknownRead++;
      else mapped.unknownWrite++;
      return;
    }
    // A trailing deletion is located at the end boundary of the result.
    const start = position.boundary && position.start >= total! ? Math.max(0, total! - 1) : position.start;
    const end = position.boundary && position.start >= total! ? total! : position.end;
    if (!position.wholeFile && start >= total!) {
      if (kind === 'read') mapped.unknownRead++;
      else mapped.unknownWrite++;
      return;
    }
    mapped.approximate = true;
    for (let index = 0; index < 10; index++) {
      const cellStart = position.wholeFile ? 0 : (index * total!) / 10;
      const cellEnd = position.wholeFile ? 1 : ((index + 1) * total!) / 10;
      if (position.wholeFile || (start < cellEnd && end > cellStart)) cells[index] = true;
    }
  };
  evidence.forEach(item => {
    if (item.unknownRead) mapped.unknownRead++;
    if (item.unknownWrite) mapped.unknownWrite++;
    item.reads.forEach(position => mark(position, mapped.readCells, 'read'));
    item.added.forEach(position => mark(position, mapped.addedCells, 'write'));
    item.deleted.forEach(position => mark(position, mapped.deletedCells, 'write'));
  });
  return mapped;
}

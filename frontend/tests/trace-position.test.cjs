const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const ts = require('typescript');

const source = readFileSync(resolve(__dirname, '../src/pages/Chat/utils/tracePosition.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
} });
const compiled = {};
new Function('exports', outputText)(compiled);
const { extractTracePosition, mapPositionSegments, extractTraceCoverage, mapTraceCoverage } = compiled;

test('read_file offset and limit map to their actual tenth, including a late range', () => {
  const first = extractTracePosition('read_file', '{"file_path":"/docs/a.md","offset":0,"limit":10}');
  const late = extractTracePosition('read_file', '{"file_path":"/docs/a.md","offset":80,"limit":10}');
  assert.deepEqual([first.start, first.end, late.start, late.end], [0, 10, 80, 90]);
  assert.deepEqual(mapPositionSegments([{ kind: 'read', position: late }], { lines: 100 }).cells,
    ['empty', 'empty', 'empty', 'empty', 'empty', 'empty', 'empty', 'empty', 'read', 'empty']);
});

test('explicit one-based line range and a unified diff hunk use real line positions', () => {
  const explicit = extractTracePosition('read_file', '{"start_line":71,"end_line":72}');
  const diff = extractTracePosition('edit_file', '{"old_string":"x","new_string":"y"}',
    [{ path: '/docs/a.md', diff: '@@ -71,2 +81,3 @@\n-old\n+new' }]);
  assert.deepEqual([explicit.start, explicit.end, diff.start, diff.end], [70, 72, 80, 83]);
  assert.equal(mapPositionSegments([{ kind: 'write', position: diff }], { lines: 100 }).cells[8], 'write');
});

test('read and write overlap is visible as both; whole-file writes cover all ten cells', () => {
  const read = extractTracePosition('read_file', '{"offset":40,"limit":10}');
  const write = extractTracePosition('write_file', '{"file_path":"/docs/a.md","content":"new"}');
  const mapped = mapPositionSegments([{ kind: 'read', position: read }, { kind: 'write', position: write }], { lines: 100 });
  assert.equal(mapped.cells[4], 'both');
  assert.equal(mapped.cells.filter(cell => cell === 'write').length, 9);
  assert.equal(mapped.unknown, 0);
  assert.equal(mapped.approximate, true);
});

test('unknown edits, pagination offsets, and ranges without totals are never packed at the left', () => {
  assert.equal(extractTracePosition('edit_file', '{"old_string":"x","new_string":"y"}'), undefined);
  assert.equal(extractTracePosition('read_service_record', '{"offset":80,"limit":10}'), undefined);
  const read = extractTracePosition('read_file', '{"offset":80,"limit":10}');
  const mapped = mapPositionSegments([
    { kind: 'write' }, { kind: 'read', position: read },
  ], {});
  assert.deepEqual(mapped.cells, Array(10).fill('empty'));
  assert.equal(mapped.unknown, 2);
  assert.equal(mapped.approximate, false);
});

test('multiple diff hunks are left unknown rather than coloring untouched lines between them', () => {
  const diff = '@@ -1 +1 @@\n-a\n+b\n@@ -91 +91 @@\n-c\n+d';
  assert.equal(extractTracePosition('edit_file', '{}', [{ path: '/docs/a.md', diff }]), undefined);
});

test('read coverage occupies an independent thin track at the recorded line offset', () => {
  const evidence = extractTraceCoverage('read_file', '{"file_path":"/docs/a.md","offset":75,"limit":5}');
  assert.deepEqual(evidence.reads.map(({ start, end }) => [start, end]), [[75, 80]]);
  const mapped = mapTraceCoverage([evidence], { lines: 100 });
  assert.deepEqual(mapped.readCells, [false, false, false, false, false, false, false, true, false, false]);
  assert.deepEqual(mapped.addedCells, Array(10).fill(false));
  assert.deepEqual(mapped.deletedCells, Array(10).fill(false));
  assert.equal(mapped.unknownRead, 0);
});

test('multi-hunk diff locates additions and deletion boundaries without coloring the gap', () => {
  const diff = [
    '@@ -11,3 +11,3 @@',
    ' context',
    '-old',
    '+new',
    ' tail',
    '@@ -91,2 +91,3 @@',
    ' context',
    '+inserted',
    ' tail',
  ].join('\n');
  const evidence = extractTraceCoverage('edit_file', '{"file_path":"/docs/a.md"}', [{ path: '/docs/a.md', diff }]);
  assert.deepEqual(evidence.added.map(({ start, end }) => [start, end]), [[11, 12], [91, 92]]);
  assert.deepEqual(evidence.deleted.map(({ start, end, boundary }) => [start, end, boundary]), [[11, 12, true]]);
  const mapped = mapTraceCoverage([evidence], { lines: 100 });
  assert.equal(mapped.addedCells[1], true);
  assert.equal(mapped.addedCells[9], true);
  assert.equal(mapped.deletedCells[1], true);
  assert.equal(mapped.addedCells.slice(2, 9).some(Boolean), false);
  assert.equal(mapped.deletedCells.slice(2).some(Boolean), false);
  assert.equal(mapped.unknownWrite, 0);
});

test('whole-file write marks added or written content, never an invented deletion', () => {
  const evidence = extractTraceCoverage('write_file', '{"file_path":"/docs/a.md","content":"replacement"}');
  const mapped = mapTraceCoverage([evidence], {});
  assert.deepEqual(mapped.addedCells, Array(10).fill(true));
  assert.deepEqual(mapped.deletedCells, Array(10).fill(false));
  assert.equal(mapped.unknownWrite, 0);
});

test('trailing deletion maps to the last bucket of the resulting file', () => {
  const diff = '@@ -99,2 +99,1 @@\n context\n-last';
  const evidence = extractTraceCoverage('edit_file', '{}', [{ path: '/docs/a.md', diff }]);
  const mapped = mapTraceCoverage([evidence], { lines: 99 });
  assert.equal(mapped.deletedCells[9], true);
  assert.equal(mapped.unknownWrite, 0);
});

test('unlocated edits and unavailable file totals stay unknown, without fabricated left coverage', () => {
  const edit = extractTraceCoverage('edit_file', '{"old_string":"x","new_string":"y"}');
  const read = extractTraceCoverage('read_file', '{"offset":80,"limit":10}');
  const mapped = mapTraceCoverage([edit, read], {});
  assert.deepEqual(mapped.readCells, Array(10).fill(false));
  assert.deepEqual(mapped.addedCells, Array(10).fill(false));
  assert.deepEqual(mapped.deletedCells, Array(10).fill(false));
  assert.equal(mapped.unknownRead, 1);
  assert.equal(mapped.unknownWrite, 1);
});

test('explicit byte ranges locate writes while ambiguous pagination offsets remain unknown', () => {
  const write = extractTraceCoverage('edit_file', '{"input":{"file_path":"/docs/a.md","byte_offset":900,"byte_limit":20}}');
  const mapped = mapTraceCoverage([write], { bytes: 1000 });
  assert.deepEqual(mapped.addedCells, [false, false, false, false, false, false, false, false, false, true]);
  assert.equal(mapped.unknownWrite, 0);
  const ambiguous = extractTraceCoverage('edit_file', '{"offset":900,"limit":20}');
  assert.equal(ambiguous.unknownWrite, true);
});

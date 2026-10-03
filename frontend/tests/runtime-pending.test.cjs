const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const ts = require('typescript');

const src = path => readFileSync(resolve(__dirname, '../src/pages/Chat', path), 'utf8');
const compile = code => ts.transpileModule(code, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} }).outputText;

function productionPending() {
  const exports = {};
  new Function('require', 'exports', compile(src('types/runtimePending.ts')))(() => {}, exports);
  return exports;
}

function productionSend(bindings) {
  const file = ts.createSourceFile('RuntimeConversation.tsx', src('components/RuntimeConversation.tsx'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const component = file.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'RuntimeConversation');
  const send = component.body.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'send');
  assert(send, 'production send() must exist');
  const context = { submittingRef: { current: false }, ...bindings };
  return new Function(...Object.keys(context), `${compile(send.getText(file))}\nreturn send;`)(...Object.values(context));
}

function productionMergeRunSnapshots() {
  const file = ts.createSourceFile('RuntimeConversation.tsx', src('components/RuntimeConversation.tsx'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const merge = file.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'mergeRunSnapshots');
  assert(merge, 'production snapshot merge must exist');
  return new Function(`${compile(merge.getText(file))}\nreturn mergeRunSnapshots;`)();
}

function storage() {
  const records = new Map();
  return {
    getItem: key => records.get(key) || null,
    setItem: (key, value) => records.set(key, value),
    removeItem: key => records.delete(key),
  };
}

test('lost POST receipt survives remount and retries with the original request ID', async () => {
  global.sessionStorage = storage();
  const pendingApi = productionPending();
  const key = pendingApi.runtimePendingStorageKey('actor-a');
  const sent = [];
  const drafts = new Map();
  let text = 'Please check this';
  let busy = false;
  const requestRef = { current: null };
  const base = {
    override: undefined, attachments: [], session: { binding: { model: 'm' } }, model: null,
    initialSubmission: undefined, active: undefined, queueItems: [], queueInFlight: false,
    onQueueSubmit: () => assert.fail('must send directly'), inputRef: { current: null },
    t: () => '', getYoloMode: () => false, conversationId: 'conversation-1',
    setError: () => {}, setBusy: value => { busy = value; }, setRuns: () => {},
    setSubmittingRequestId: () => {},
    rememberDraft: (id, draft) => drafts.set(id, draft),
    clearAcceptedDraft: id => drafts.delete(id), atBottom: { current: false },
    onChangedRef: { current: () => {} },
    sameAttachments: (left, right) => left.length === right.length,
    crypto: { randomUUID: () => '11111111-1111-4111-8111-111111111111' },
    setText: update => { text = typeof update === 'function' ? update(text) : update; },
    setAttachments: () => {},
  };
  const onPendingStart = item => pendingApi.writeRuntimePending(key, { [item.conversationId]: item });
  const first = productionSend({ ...base, text, busy, requestRef, pending: undefined, onPendingStart,
    runtime: { turn: async (_cid, id) => { sent.push(id); throw new Error('response lost'); } },
    refresh: async () => { throw new Error('status unavailable'); }, onPendingResolved: () => assert.fail('unconfirmed'),
  });
  await first();
  const restored = pendingApi.readRuntimePending(key)['conversation-1'];
  assert.equal(restored.requestId, sent[0]);
  assert.equal(restored.text, text);
  assert.equal(drafts.get('conversation-1').text, text);

  // A new component instance has no requestRef, just the restored pending map.
  const afterRemount = { current: null };
  const second = productionSend({ ...base, text, busy: false, requestRef: afterRemount, pending: restored,
    onPendingStart: () => assert.fail('retry must not mint a new request'),
    runtime: { turn: async (_cid, id) => { sent.push(id); return { id: 'run-1', request_id: id }; } },
    refresh: async () => assert.fail('success should not refresh'),
    onPendingResolved: id => {
      assert.equal(id, restored.requestId);
      pendingApi.writeRuntimePending(key, {});
    },
  });
  await second();
  assert.deepEqual(sent, [restored.requestId, restored.requestId]);
  assert.equal(text, '');
  assert.equal(drafts.has('conversation-1'), false);
  assert.deepEqual(pendingApi.readRuntimePending(key), {});
});

test('first message and attachments restore after reload; storage quota failure prevents POST', async () => {
  global.sessionStorage = storage();
  const pendingApi = productionPending();
  const key = pendingApi.runtimePendingStorageKey('actor-b');
  const initial = {
    conversationId: 'conversation-2', requestId: '22222222-2222-4222-8222-222222222222',
    text: 'Review attachment', model: 'm', yolo: true,
    attachments: [{ name: 'note.txt', dataUrl: 'data:text/plain;base64,aGk=' }], kind: 'initial',
  };
  assert.equal(pendingApi.writeRuntimePending(key, { [initial.conversationId]: initial }), true);
  const restored = pendingApi.readRuntimePending(key)[initial.conversationId];
  assert.deepEqual(restored, initial);
  assert.equal(pendingApi.pendingAccepted(restored, [{ request_id: initial.requestId }]), true);

  global.sessionStorage.setItem = () => { throw new Error('quota'); };
  assert.equal(pendingApi.writeRuntimePending(key, { [initial.conversationId]: initial }), false);
  let posted = false;
  const send = productionSend({
    text: 'new message', attachments: [], busy: false, session: { binding: { model: 'm' } }, model: null,
    requestRef: { current: null }, pending: undefined, initialSubmission: undefined,
    active: undefined, queueItems: [], queueInFlight: false, t: () => '',
    sameAttachments: (left, right) => left.length === right.length,
    onQueueSubmit: () => {}, inputRef: { current: null }, getYoloMode: () => false,
    crypto: { randomUUID: () => '33333333-3333-4333-8333-333333333333' },
    onPendingStart: item => pendingApi.writeRuntimePending(key, { [item.conversationId]: item }),
    conversationId: 'conversation-3', setError: () => {},
    runtime: { turn: async () => { posted = true; } },
  });
  await send();
  assert.equal(posted, false);
});

test('a live first-turn POST is not mistaken for a recovered uncertain submission', () => {
  const { shouldRestoreRuntimePending } = productionPending();
  const pending = {
    conversationId: 'conversation-1', requestId: '11111111-1111-4111-8111-111111111111',
    text: 'First message', model: 'm', yolo: false, attachments: [], kind: 'initial',
  };
  assert.equal(shouldRestoreRuntimePending(pending, [], undefined,
    { requestId: pending.requestId, status: 'sending' }), false);
  assert.equal(shouldRestoreRuntimePending(pending, [], undefined,
    { requestId: pending.requestId, status: 'submitted' }), false);
  assert.equal(shouldRestoreRuntimePending(pending, [], undefined,
    { requestId: pending.requestId, status: 'failed' }), true);
  assert.equal(shouldRestoreRuntimePending(pending, [], undefined, undefined), true,
    'after a reload the pending turn must still be recoverable');
  assert.equal(shouldRestoreRuntimePending(pending, [{ request_id: pending.requestId }], undefined,
    { requestId: pending.requestId, status: 'sending' }), false);
});

test('delayed CLI admission shows the submitted turn immediately and reconciles one server run', async () => {
  let completePost;
  const post = new Promise(resolve => { completePost = resolve; });
  const requestId = '44444444-4444-4444-8444-444444444444';
  const attachments = [{ name: 'note.txt', dataUrl: 'data:text/plain;base64,aGk=' }];
  let composer = 'Check this note';
  let files = attachments;
  let submitting = null;
  let busy = false;
  let runs = [];
  let pending = null;
  let turns = 0;
  const requestRef = { current: null };
  const submittingRef = { current: false };
  const send = productionSend({
    text: composer, attachments, busy, session: { binding: { model: 'm' } }, model: null,
    requestRef, submittingRef, pending: undefined, initialSubmission: undefined,
    active: undefined, queueItems: [], queueInFlight: false, t: () => '',
    sameAttachments: (left, right) => left.length === right.length && left.every((item, index) => item.dataUrl === right[index].dataUrl),
    onQueueSubmit: () => assert.fail('direct send must not queue'), inputRef: { current: null }, getYoloMode: () => false,
    crypto: { randomUUID: () => requestId }, conversationId: 'conversation-4',
    onPendingStart: item => { pending = item; return true; },
    setText: update => { composer = typeof update === 'function' ? update(composer) : update; },
    setAttachments: update => { files = typeof update === 'function' ? update(files) : update; },
    setSubmittingRequestId: value => { submitting = value; },
    setBusy: value => { busy = value; }, setError: () => {},
    setRuns: update => { runs = update(runs); },
    rememberDraft: () => {}, clearAcceptedDraft: () => {},
    atBottom: { current: false }, onChangedRef: { current: () => {} },
    runtime: { turn: async (_conversationId, id, message, _model, inputs) => {
      turns++;
      assert.equal(id, requestId);
      assert.equal(message, 'Check this note');
      assert.deepEqual(inputs, [{ name: 'note.txt', data_url: attachments[0].dataUrl }]);
      return post;
    } },
    refresh: async () => assert.fail('successful POST does not need reconciliation'),
    onPendingResolved: id => { assert.equal(id, requestId); pending = null; },
  });

  const inFlight = send();
  assert.equal(pending?.requestId, requestId);
  assert.equal(submitting, requestId);
  assert.equal(composer, '');
  assert.deepEqual(files, []);
  assert.equal(busy, true);
  assert.deepEqual(runs, []);
  await send();
  assert.equal(turns, 1, 'same-render repeated send must not create a second POST');

  completePost({ id: 'run-4', request_id: requestId, status: 'starting' });
  await inFlight;
  assert.deepEqual(runs.map(run => run.id), ['run-4']);
  assert.equal(submitting, null);
  assert.equal(pending, null);
  assert.equal(busy, false);
  assert.equal(submittingRef.current, false);
  assert.equal(composer, '');
});

test('an older session GET cannot erase an accepted run or newer stream events', () => {
  const merge = productionMergeRunSnapshots();
  const accepted = { id: 'run-1', request_id: 'request-1', created_at: 1, seq: 3, status: 'running' };
  assert.deepEqual(merge([accepted], []), [accepted]);
  assert.deepEqual(merge([accepted], [{ ...accepted, seq: 1, status: 'starting' }]), [accepted]);
  const completed = { ...accepted, seq: 4, status: 'completed' };
  assert.deepEqual(merge([accepted], [completed]), [completed]);
});

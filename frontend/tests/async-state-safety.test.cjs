const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const ts = require('typescript');

const source = path => readFileSync(resolve(__dirname, '../src', path), 'utf8');
const compile = code => ts.transpileModule(code, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
} }).outputText;
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

// Execute the production Providers with controlled hook state, API promises and
// RAF. This is a state/async regression harness, not a DOM or browser test.
function mount(path, name, api) {
  const slots = []; let cursor = 0, value;
  const confirmations = [], notices = [], frames = new Map(); let frameId = 0;
  const sameDeps = (a, b) => a && b && a.length === b.length && a.every((x, i) => Object.is(x, b[i]));
  const memo = (factory, deps) => {
    const i = cursor++, old = slots[i];
    if (!old || !sameDeps(old.deps, deps)) slots[i] = { deps, value: factory() };
    return slots[i].value;
  };
  const react = {
    createContext: () => ({ Provider: 'Provider' }), useContext: () => {},
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      const slot = slots[i];
      slot.set ||= next => { slot.value = typeof next === 'function' ? next(slot.value) : next; };
      return [slot.value, slot.set];
    },
    useRef(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initial }; return slots[i]; },
    useCallback: (fn, deps) => memo(() => fn, deps), useMemo: memo,
    useEffect: () => { cursor++; },
  };
  const message = { success: text => notices.push(['success', text]), error: text => notices.push(['error', text]) };
  const modal = { confirm: options => confirmations.push(options) };
  const exports = {};
  const dependencies = {
    react, 'react/jsx-runtime': { jsx: (_, props) => { value = props.value; return props; } },
    antd: { App: { useApp: () => ({ message, modal }) } },
    '../services/api': api,
    '../utils/fileKind': { getFileKind: () => 'text', shouldLoadText: () => true },
    '../utils/recentFiles': { pushRecentFile: () => {} },
    '../pages/Chat/streamFlush': { buildFingerprintedBlocks: blocks => ({ next: [...blocks], nextFp: [] }) },
  };
  global.localStorage = { getItem: () => null, setItem: () => {} };
  global.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
  global.cancelAnimationFrame = id => frames.delete(id);
  new Function('require', 'exports', compile(source(path)))(name => {
    assert(name in dependencies, `Unexpected dependency: ${name}`); return dependencies[name];
  }, exports);
  return { confirmations, notices,
    render() { cursor = 0; exports[name]({ children: null }); return value; },
    flush() { for (const [id, callback] of frames) { frames.delete(id); callback(); } },
  };
}
function workspace() {
  const writes = [];
  const instance = mount('stores/fileWorkspaceContext.tsx', 'FileWorkspaceProvider', {
    readFile: async () => ({ content: 'original' }),
    writeFile(path, content) { const response = deferred(); writes.push({ path, content, ...response }); return response.promise; },
  });
  return { ...instance, writes, async edit(content, path = '/docs/a.txt') {
    await instance.render().openFile(path); instance.render().setEditContent(content); instance.render();
  } };
}
function streams() {
  const requests = [], stops = [], stopResponse = deferred(), stopResponses = [];
  const instance = mount('stores/streamContext.tsx', 'StreamProvider', {
    streamChat: (id, content, callbacks) => requests.push({ id, callbacks, content }),
    resumeChat: (id, decisions, callbacks) => requests.push({ id, callbacks, decisions }),
    stopChat: id => {
      const response = stopResponses.length ? deferred() : stopResponse;
      stopResponses.push(response); stops.push(id); return response.promise;
    },
  });
  return { ...instance, requests, stops, stopResponse, stopResponses };
}

// Compile the actual page callback rather than duplicating its async logic.
function pageCallback(name, bindings) {
  const file = ts.createSourceFile('Chat.tsx', source('pages/Chat/index.tsx'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const page = file.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'ChatPage');
  const fn = page.body.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
  const declaration = page.body.statements.filter(ts.isVariableStatement)
    .flatMap(statement => [...statement.declarationList.declarations])
    .find(node => node.name.getText(file) === name);
  const callback = declaration?.initializer?.arguments?.[0];
  assert(fn || callback, `Missing production callback ${name}`);
  const code = fn ? fn.getText(file) : `const ${name} = ${callback.getText(file)};`;
  const env = {
    pendingDeepagentSendsRef: { current: new Map() },
    setFailedDeepagentSends: update => update({}),
    ...bindings,
  };
  return new Function(...Object.keys(env), compile(code) + `\nreturn ${name};`)(...Object.values(env));
}

test('save receipt leaves edits made during the request dirty and close still confirms', async () => {
  const w = workspace(); await w.edit('v1');
  const save = w.render().saveFile(); await tick();
  w.render().setEditContent('v2'); w.render();
  w.writes[0].resolve(); await save;
  assert.equal(w.writes[0].content, 'v1');
  assert.equal(w.render().editContent, 'v2'); assert.equal(w.render().editDirty, true);
  assert.equal(w.notices[0][1], '已保存此前版本，新增修改尚未保存');
  w.render().closeTab('/docs/a.txt'); assert.equal(w.confirmations.length, 1);
  assert.equal(w.render().openTabs.length, 1);
});

test('explicit saves queue snapshots in order and duplicate shortcuts do not write twice', async () => {
  const w = workspace(); await w.edit('v1');
  const first = w.render().saveFile(); const duplicate = w.render().saveFile(); await tick();
  w.render().setEditContent('v2'); const second = w.render().saveFile(); await tick();
  assert.equal(w.writes.length, 1);
  w.writes[0].resolve(); await first; await duplicate; await tick();
  assert.equal(w.writes.length, 2); assert.equal(w.writes[1].content, 'v2');
  assert.equal(w.render().editDirty, true); assert.equal(w.render().saving, true);
  w.writes[1].resolve(); await second;
  assert.equal(w.render().editDirty, false); assert.equal(w.render().saving, false);
});

test('failed save preserves dirty state and a queued newer save can still succeed', async () => {
  const w = workspace(); await w.edit('v1'); const first = w.render().saveFile(); await tick();
  w.render().setEditContent('v2'); const second = w.render().saveFile();
  w.writes[0].reject(new Error('offline')); await first; await tick();
  assert.equal(w.render().editDirty, true); assert.equal(w.notices[0][0], 'error');
  w.writes[1].resolve(); await second;
  assert.equal(w.render().editDirty, false);
});

test('receipt for a closed tab cannot clear a reopened tab with the same revision', async () => {
  const w = workspace(); await w.edit('v1'); const save = w.render().saveFile(); await tick();
  w.render().closeTab('/docs/a.txt', true); w.render(); await w.edit('new v1');
  w.writes[0].resolve(); await save;
  assert.equal(w.render().editContent, 'new v1'); assert.equal(w.render().editDirty, true);
});

test('saving is tracked per file while independent file writes may run concurrently', async () => {
  const w = workspace(); await w.edit('A'); const a = w.render().saveFile(); await tick();
  await w.edit('B', '/docs/b.txt'); assert.equal(w.render().saving, false);
  const b = w.render().saveFile(); await tick(); assert.equal(w.writes.length, 2);
  w.writes[0].resolve(); await a; assert.equal(w.render().saving, true);
  w.writes[1].resolve(); await b; assert.equal(w.render().saving, false);
});

for (const previous of [false, true]) test(`restored approval binds completion and stop to B (previous A: ${previous})`, async () => {
  const s = streams(), completions = [];
  if (previous) {
    let old; s.render().startStream('A', 'old', { onDone: (_, id) => { old = id; } });
    s.requests[0].callbacks.onDone(); assert(s.render().clearFinished(old));
  }
  s.render().restoreInterrupt('B', { actions: [{}], configs: [] });
  s.render().resumeStream('B', [{ type: 'approve' }], { onDone: (id, identity) => completions.push({ id, identity }) });
  s.requests.at(-1).callbacks.onDone();
  assert.equal(completions[0].id, 'B'); assert.equal(completions[0].identity.conversationId, 'B');
  const stopped = s.render().stopStream(); assert.deepEqual(s.stops, ['B']);
  s.stopResponse.resolve({ status: 'stopped' }); const { identity } = await stopped;
  assert.equal(identity.conversationId, 'B'); assert(s.render().clearFinished(identity));
});

test('buffered events from an old reader cannot mutate, finish, or interrupt the new run', () => {
  const s = streams(), done = [];
  s.render().startStream('A', 'first', { onDone: id => done.push(id) }); const old = s.requests[0].callbacks;
  s.render().startStream('B', 'second', { onDone: id => done.push(id) }); const current = s.requests[1].callbacks;
  current.onToken('B only'); old.onToken('stale A'); old.onError('stale error'); old.onInterrupt([{}], []); old.onDone();
  assert.equal(s.render().isStreaming, true); assert.equal(s.render().interruptData, null);
  current.onDone(); assert.deepEqual(done, ['B']);
  assert.deepEqual(s.render().streamBlocks, [{ type: 'text', content: 'B only' }]);
});

test('late approval restoration cannot overwrite an active stream', () => {
  const s = streams(); s.render().startStream('B', 'running', {});
  s.render().restoreInterrupt('A', { actions: [{}], configs: [] });
  assert.equal(s.render().streamingConvId, 'B'); assert.equal(s.render().isStreaming, true);
});

for (const nextConversation of ['B', 'A']) test(`late A history response preserves new ${nextConversation} run and its queued input`, async () => {
  const s = streams(), history = deferred(), visible = [], queued = [];
  const onDone = pageCallback('handleStreamDone', {
    api: { getConversation: () => history.promise }, loadConversations: () => {}, checkServerStreaming: () => {},
    currentConvIdRef: { current: nextConversation }, setMessages: messages => visible.push(messages),
    isCurrentStream: id => s.render().isCurrentStream(id), clearFinished: id => s.render().clearFinished(id),
    processNextQueuedMessage: id => queued.push(id),
  });
  s.render().startStream('A', 'first', { onDone }); s.requests[0].callbacks.onDone();
  s.render().startStream(nextConversation, 'second', {});
  history.resolve({ messages: ['old history'] }); await tick();
  assert.equal(s.render().streamingConvId, nextConversation); assert.equal(s.render().isStreaming, true);
  assert.deepEqual(visible, []); assert.deepEqual(queued, []);
});

test('matching completion still commits history, clears its run, and advances its queue', async () => {
  const s = streams(), history = deferred(), visible = [], queued = [];
  const onDone = pageCallback('handleStreamDone', {
    api: { getConversation: () => history.promise }, loadConversations: () => {}, checkServerStreaming: () => {},
    currentConvIdRef: { current: 'A' }, setMessages: messages => visible.push(messages),
    isCurrentStream: id => s.render().isCurrentStream(id), clearFinished: id => s.render().clearFinished(id),
    processNextQueuedMessage: id => queued.push(id),
  });
  s.render().startStream('A', 'first', { onDone }); s.requests[0].callbacks.onDone();
  history.resolve({ messages: ['saved answer'] }); await tick();
  assert.equal(s.render().streamingConvId, null); assert.deepEqual(visible, [['saved answer']]);
  assert.deepEqual(queued, ['A']);
});

test('late error recovery cannot clear a newer run', async () => {
  const s = streams(), history = deferred(), timers = [], visible = [];
  const onError = pageCallback('handleStreamError', {
    api: { getConversation: () => history.promise }, loadConversations: () => {}, checkServerStreaming: () => {},
    currentConvIdRef: { current: 'A' }, setMessages: messages => visible.push(messages),
    isCurrentStream: id => s.render().isCurrentStream(id), clearFinished: id => s.render().clearFinished(id),
    setTimeout: callback => timers.push(callback),
  });
  s.render().startStream('A', 'first', { onError }); s.requests[0].callbacks.onError('offline'); timers[0]();
  s.render().startStream('A', 'retry', {}); history.resolve({ messages: ['old history'] }); await tick();
  assert.equal(s.render().streamingConvId, 'A'); assert.equal(s.render().isStreaming, true); assert.deepEqual(visible, []);
});

test('late stop history refresh cannot clear a newer run or accept old reader events', async () => {
  const s = streams(), history = deferred(), timers = [], visible = [];
  s.render().startStream('A', 'first', {}); const old = s.requests[0].callbacks;
  old.onInterrupt([{}], []);
  const stop = pageCallback('handleStop', {
    isStreaming: true, stopStream: () => s.render().stopStream(), resetScroll: () => {},
    api: { getConversation: () => history.promise }, loadConversations: () => {}, checkServerStreaming: () => {},
    currentConvIdRef: { current: 'A' }, setMessages: messages => visible.push(messages),
    isCurrentStream: id => s.render().isCurrentStream(id), clearFinished: id => s.render().clearFinished(id),
    setTimeout: callback => timers.push(callback),
  });
  const stopping = stop(); s.stopResponse.resolve({ status: 'stopped' }); await tick();
  s.render().startStream('A', 'retry', {}); old.onDone(); old.onToken('obsolete');
  history.resolve({ messages: ['old history'] }); await stopping;
  assert.equal(s.render().streamingConvId, 'A'); assert.equal(s.render().isStreaming, true); assert.deepEqual(visible, []);
});


test('continued-turn history is fenced when the same conversation starts another run', async () => {
  const s = streams(), history = deferred(), visible = [], removed = [];
  const onRunContinued = pageCallback('handleRunContinued', {
    api: { getConversation: () => history.promise }, currentConvIdRef: { current: 'A' },
    setMessages: messages => visible.push(messages), removeQueueItem: (_, id) => removed.push(id),
    isCurrentStream: id => s.render().isCurrentStream(id),
  });
  s.render().startStream('A', 'first', { onRunContinued });
  s.requests[0].callbacks.onRunContinued('follow-up', 'queue-1');
  s.render().startStream('A', 'second', {});
  history.resolve({ messages: ['old history'] }); await tick();
  assert.deepEqual(removed, ['queue-1']); assert.deepEqual(visible, []);
});

test('deleting an approval conversation clears only its captured stop identity', async () => {
  const s = streams(), deletion = deferred(), removed = [], errors = [];
  s.render().restoreInterrupt('A', { actions: [{}], configs: [] });
  const remove = pageCallback('handleDeleteConv', {
    streamingConvId: 'A', isStreaming: false, interruptData: { actions: [{}] },
    stopStream: () => s.render().stopStream(), clearFinished: id => s.render().clearFinished(id),
    api: { deleteConversation: () => deletion.promise },
    runtimeSessionForIdRef: { current: new Map([['A', 'session-a']]) },
    runtimeQueueRetryAtRef: { current: new Map([['A', 0]]) },
    commitRuntimeQueues: update => update({ A: [] }),
    commitRuntimePending: update => update({ A: { requestId: 'request-a' } }),
    setRuntimeQueueErrors: update => update({ A: 'error' }),
    setRuntimeInitialSubmission: update => update(null),
    setConversations: updater => removed.push(updater([{ id: 'A' }, { id: 'B' }])),
    currentConvIdRef: { current: 'B' }, messageApi: { error: error => errors.push(error) }, t: x => x,
  });
  const pending = remove('A'); s.stopResponse.resolve({ status: 'stopped' }); await tick();
  s.render().startStream('B', 'new', {}); deletion.resolve(); await pending;
  assert.deepEqual(s.stops, ['A']); assert.deepEqual(errors, []);
  assert.deepEqual(removed, [[{ id: 'B' }]]); assert.equal(s.render().streamingConvId, 'B');
  assert.equal(s.render().isStreaming, true);
});


test('failed stop keeps stream and output alive, exposes failure, and permits retry', async () => {
  const s = streams(); s.render().startStream('A', 'running', {});
  const first = s.render().stopStream(); assert.equal(s.render().stopState, 'requesting');
  s.stopResponse.reject(new Error('stop is offline')); assert.equal(await first, null);
  assert.equal(s.render().stopState, 'failed'); assert.equal(s.render().stopError, 'stop is offline');
  assert.equal(s.render().isStreaming, true);
  s.requests[0].callbacks.onToken('still producing'); s.flush();
  assert.deepEqual(s.render().streamBlocks, [{ type: 'text', content: 'still producing' }]);
  const retry = s.render().stopStream(); assert.deepEqual(s.stops, ['A', 'A']);
  s.stopResponses[1].resolve({ status: 'stopping' }); await retry;
  assert.equal(s.render().stopState, 'requested'); assert.equal(s.render().stopError, null);
  assert.equal(s.render().isStreaming, true, 'acceptance is not termination');
  assert(!s.render().streamBlocks.some(block => block.content.includes('已中止')));
});

test('concurrent stop clicks reuse one request', async () => {
  const s = streams(); s.render().startStream('A', 'running', {});
  const first = s.render().stopStream(), second = s.render().stopStream();
  assert.deepEqual(s.stops, ['A']); s.stopResponse.resolve({ status: 'stopping' });
  assert.deepEqual(await first, await second);
});

for (const rejected of [true, false]) test(`late stop ${rejected ? 'failure' : 'acceptance'} does not change a new run`, async () => {
  const s = streams(); s.render().startStream('A', 'running', {}); const stop = s.render().stopStream();
  s.render().startStream('A', 'new run', {});
  if (rejected) s.stopResponse.reject(new Error('old failure')); else s.stopResponse.resolve({ status: 'stopped' });
  assert.equal(await stop, null); assert.equal(s.render().isStreaming, true);
  assert.equal(s.render().stopState, 'idle'); assert.equal(s.render().stopError, null);
});

for (const failed of [true, false]) test(`manual stop ${failed ? 'failure' : 'acceptance'} does not advance queued messages on completion`, async () => {
  const s = streams(), queued = [], history = deferred();
  const onDone = pageCallback('handleStreamDone', {
    api: { getConversation: () => history.promise }, loadConversations: () => {}, checkServerStreaming: () => {},
    currentConvIdRef: { current: 'A' }, setMessages: () => {},
    isCurrentStream: id => s.render().isCurrentStream(id), clearFinished: id => s.render().clearFinished(id),
    processNextQueuedMessage: id => queued.push(id),
  });
  s.render().startStream('A', 'running', { onDone }); const stop = s.render().stopStream();
  if (failed) s.stopResponse.reject(new Error('offline')); else s.stopResponse.resolve({ status: 'stopping' });
  await stop; s.requests[0].callbacks.onDone(); history.resolve({ messages: [] }); await tick();
  assert.deepEqual(queued, []); assert.equal(s.render().streamingConvId, null);
});

for (const failed of [true, false]) test(`delete is deferred when stop ${failed ? 'fails' : 'is only accepted'}`, async () => {
  const s = streams(), deleted = [], notices = [];
  s.render().startStream('A', 'running', {});
  const remove = pageCallback('handleDeleteConv', {
    streamingConvId: 'A', isStreaming: true, interruptData: null,
    stopStream: () => s.render().stopStream(), clearFinished: id => s.render().clearFinished(id),
    api: { deleteConversation: id => deleted.push(id) }, checkServerStreaming: () => {},
    messageApi: { error: error => notices.push(error), info: text => notices.push(text) }, t: x => x,
  });
  const deleting = remove('A');
  if (failed) s.stopResponse.reject(new Error('offline')); else s.stopResponse.resolve({ status: 'stopping' });
  await deleting; assert.deepEqual(deleted, []); assert.equal(s.render().isStreaming, true);
  if (!failed) assert.deepEqual(notices, ['chat.stopBeforeDelete']);
});

test('interrupt failure keeps the queue untouched and reports the error', async () => {
  const errors = [], calls = [], pending = deferred();
  const interrupt = pageCallback('runInterruptItem', {
    api: { stopChat: (id, options) => { calls.push({ id, options }); return pending.promise; } },
    getStreamGeneration: () => 1, messageApi: { error: text => errors.push(text), warning: () => {} }, t: x => x,
  });
  const queued = { id: 'q1', content: '  next message  ' };
  const sending = interrupt('A', queued); pending.reject(new Error('stop failed')); await sending;
  assert.deepEqual(errors, ['stop failed']); assert.deepEqual(queued, { id: 'q1', content: '  next message  ' });
  assert.deepEqual(calls, [{ id: 'A', options: { followUp: 'next message', queueId: 'q1' } }]);
});

test('late interrupt failure is ignored after another generation starts', async () => {
  const errors = [], pending = deferred(); let generation = 1;
  const interrupt = pageCallback('runInterruptItem', {
    api: { stopChat: () => pending.promise }, getStreamGeneration: () => generation,
    messageApi: { error: text => errors.push(text), warning: () => {} }, t: x => x,
  });
  const sending = interrupt('A', { id: 'q', content: 'next' }); generation++;
  pending.reject(new Error('obsolete')); await sending; assert.deepEqual(errors, []);
});

test('force stop failure stays visible without falsely announcing success', async () => {
  const errors = [], infos = [], pending = deferred();
  const stop = pageCallback('handleForceStop', {
    api: { stopChat: () => pending.promise }, getStreamGeneration: () => 1,
    messageApi: { error: text => errors.push(text), info: text => infos.push(text) }, t: x => x,
  });
  const sending = stop('A'); pending.reject(new Error('server unreachable')); await sending;
  assert.deepEqual(errors, ['server unreachable']); assert.deepEqual(infos, []);
});

function apiModule(fetch) {
  const exports = {};
  const localStorage = { getItem: () => 'test-token', setItem: () => {}, removeItem: () => {} };
  new Function('exports', 'fetch', 'localStorage', 'navigator', compile(source('services/api.ts')))(
    exports, fetch, localStorage, { language: 'en' },
  );
  return exports;
}

for (const status of ['stopping', 'stopped', 'not_running']) test(`stop API preserves SSE and returns explicit ${status} status`, async () => {
  const calls = [], reader = deferred();
  const api = apiModule(async (url, options) => {
    calls.push({ url, options });
    return url.endsWith('/chat')
      ? { ok: true, body: { getReader: () => ({ read: () => reader.promise }) } }
      : { ok: true, json: async () => ({ status }) };
  });
  api.streamChat('A', 'running', {}); await tick();
  assert.deepEqual(await api.stopChat('A'), { status });
  assert.equal(calls[0].options.signal.aborted, false);
  assert.deepEqual(JSON.parse(calls[1].options.body), { conversation_id: 'A' });
  reader.resolve({ done: true }); await tick();
});

test('stop API propagates HTTP failure and leaves the active reader connected', async () => {
  const calls = [], reader = deferred();
  const api = apiModule(async (url, options) => {
    calls.push({ url, options });
    return url.endsWith('/chat')
      ? { ok: true, body: { getReader: () => ({ read: () => reader.promise }) } }
      : { ok: false, status: 503, json: async () => ({ detail: 'try again' }) };
  });
  api.streamChat('A', 'running', {}); await tick();
  await assert.rejects(api.stopChat('A'), /try again/);
  assert.equal(calls[0].options.signal.aborted, false); reader.resolve({ done: true }); await tick();
});

test('stop API rejects an unrecognized successful response instead of claiming termination', async () => {
  const api = apiModule(async () => ({ ok: true, json: async () => ({ success: true }) }));
  await assert.rejects(api.stopChat('A'), /无法确认/);
});


test('not_running during agent startup does not falsely stop the pending live request', async () => {
  const s = streams(); s.render().startStream('A', 'preparing agent', {});
  const stopping = s.render().stopStream(); s.stopResponse.resolve({ status: 'not_running' });
  assert.equal(await stopping, null); assert.equal(s.render().isStreaming, true);
  assert.equal(s.render().stopState, 'failed'); assert.match(s.render().stopError, /不能确认停止/);
});

test('an adapter stopped response still waits for a live reader to confirm completion', async () => {
  const s = streams(); s.render().startStream('A', 'running', {});
  const stopping = s.render().stopStream(); s.stopResponse.resolve({ status: 'stopped' });
  assert.equal((await stopping).pending, true); assert.equal(s.render().isStreaming, true);
  assert.equal(s.render().stopState, 'requested');
});


test('SSE EOF without a terminal receipt reports uncertainty instead of successful completion', async () => {
  const done = [], errors = [], reader = deferred();
  const api = apiModule(async () => ({ ok: true, body: { getReader: () => ({ read: () => reader.promise }) } }));
  api.streamChat('A', 'running', { onDone: () => done.push(true), onError: error => errors.push(error) });
  await tick(); reader.resolve({ done: true }); await tick();
  assert.deepEqual(done, []); assert.match(errors[0], /未收到执行结束确认/);
});

test('an explicit terminal event remains valid without a trailing newline', async () => {
  const done = [], errors = []; let reads = 0;
  const api = apiModule(async () => ({ ok: true, body: { getReader: () => ({
    read: async () => reads++ ? { done: true } : { done: false, value: new TextEncoder().encode('data: {"type":"done"}') },
  }) } }));
  api.streamChat('A', 'running', { onDone: () => done.push(true), onError: error => errors.push(error) });
  await tick(); assert.deepEqual(done, [true]); assert.deepEqual(errors, []);
});

test('an old reader finishing cannot drop the newer reader abort controller', async () => {
  const readers = [deferred(), deferred()], signals = []; let index = 0;
  const api = apiModule(async (_, options) => {
    const reader = readers[index++]; signals.push(options.signal);
    return { ok: true, body: { getReader: () => ({ read: () => reader.promise }) } };
  });
  api.streamChat('A', 'old', {}); await tick(); api.streamChat('B', 'new', {}); await tick();
  readers[0].resolve({ done: true }); await tick();
  api.abortStream(); assert.equal(signals[1].aborted, true);
  readers[1].resolve({ done: true }); await tick();
});


for (const failed of [false, true]) test(`failed ${failed ? 'error' : 'completion'} history refresh does not block another approval`, async () => {
  const s = streams(), timers = [], done = [];
  const bindings = {
    api: { getConversation: async () => { throw new Error('history offline'); } },
    loadConversations: () => {}, checkServerStreaming: () => {}, currentConvIdRef: { current: 'A' },
    setMessages: () => {}, processNextQueuedMessage: () => {}, setTimeout: callback => timers.push(callback),
    isCurrentStream: id => s.render().isCurrentStream(id), clearFinished: id => s.render().clearFinished(id),
  };
  const callback = pageCallback(failed ? 'handleStreamError' : 'handleStreamDone', bindings);
  s.render().startStream('A', 'old', failed ? { onError: callback } : { onDone: callback });
  if (failed) { s.requests[0].callbacks.onError('model failed'); timers[0](); }
  else s.requests[0].callbacks.onDone();
  await tick(); assert.equal(s.render().streamingConvId, 'A'); assert.equal(s.render().isStreaming, false);
  s.render().restoreInterrupt('B', { actions: [{ name: 'write_file' }], configs: [] });
  assert.equal(s.render().streamingConvId, 'B'); assert.equal(s.render().interruptData.actions[0].name, 'write_file');
  s.render().resumeStream('B', [{ type: 'approve' }], { onDone: id => done.push(id) });
  s.requests.at(-1).callbacks.onDone(); assert.deepEqual(done, ['B']);
});

test('restoring another approval still cannot replace an unresolved current approval', () => {
  const s = streams(); s.render().restoreInterrupt('A', { actions: [{ name: 'first' }], configs: [] });
  s.render().restoreInterrupt('B', { actions: [{ name: 'second' }], configs: [] });
  assert.equal(s.render().streamingConvId, 'A'); assert.equal(s.render().interruptData.actions[0].name, 'first');
});

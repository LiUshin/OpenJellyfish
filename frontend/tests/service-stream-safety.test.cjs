const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const ts = require('typescript');
const compile = path => ts.transpileModule(readFileSync(resolve(__dirname, '../src', path), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
class AuthError extends Error {}

// Production hook and fingerprint cloning; deferred fetch/read promises ignore
// abort deliberately so that buffered network callbacks can arrive arbitrarily.
function mount() {
  const slots = [], effects = [], requests = [], frames = new Map();
  let cursor = 0, frameId = 0, latestOpts = {}, mounted = true, writesAfterUnmount = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const react = {
    useState(initial) {
      const i = cursor++; if (!(i in slots)) slots[i] = { value: initial };
      const slot = slots[i];
      slot.set ||= value => { if (!mounted) writesAfterUnmount++; slot.value = typeof value === 'function' ? value(slot.value) : value; };
      return [slot.value, slot.set];
    },
    useRef(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initial }; return slots[i]; },
    useCallback(fn, deps) {
      const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { value: fn, deps };
      return slots[i].value;
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || !same(slots[i].deps, deps)) {
        const old = slots[i]; slots[i] = { deps, cleanup: undefined };
        effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); });
      }
    },
  };
  const streamFlush = {};
  new Function('exports', compile('pages/Chat/streamFlush.ts'))(streamFlush);
  const dependencies = {
    react, '../pages/Chat/streamFlush': streamFlush,
    './serviceApi': { AuthError, openChatStream(key, req, signal) {
      const request = { key, req, signal, ...deferred() }; requests.push(request); return request.promise;
    } },
  };
  const exports = {};
  global.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
  global.cancelAnimationFrame = id => frames.delete(id);
  new Function('require', 'exports', compile('service-chat/streamHandler.ts'))(name => {
    assert(name in dependencies, `Unexpected dependency ${name}`); return dependencies[name];
  }, exports);
  return {
    requests, frames, get writesAfterUnmount() { return writesAfterUnmount; },
    render(opts = latestOpts) {
      latestOpts = opts; cursor = 0; const value = exports.useServiceStream(opts);
      effects.splice(0).forEach(effect => effect()); return value;
    },
    flush() { for (const [id, frame] of frames) { frames.delete(id); frame(); } },
    unmount() { mounted = false; for (const slot of slots) slot?.cleanup?.(); },
  };
}
function pipe() {
  const reads = []; let released = 0, cancelled = 0, opened = 0;
  const reader = { read() { const read = deferred(); reads.push(read); return read.promise; }, releaseLock() { released++; } };
  return {
    reads, get released() { return released; }, get cancelled() { return cancelled; }, get opened() { return opened; },
    response: { ok: true, status: 200, body: { getReader() { opened++; return reader; }, async cancel() { cancelled++; } } },
    token(value) { reads.at(-1).resolve({ done: false, value: new TextEncoder().encode(`data: ${JSON.stringify({ type: 'token', content: value })}\n\n`) }); },
    done() { reads.at(-1).resolve({ done: true }); }, fail(error) { reads.at(-1).reject(error); },
  };
}
const req = id => ({ conversation_id: id, message: 'hello' });
const text = blocks => blocks.filter(b => b.type === 'text').map(b => b.content).join('');
const callbacks = (id, log) => ({ onDone: blocks => log.push([id, 'done', text(blocks)]),
  onError: error => log.push([id, 'error', error]), onAuthError: () => log.push([id, 'auth']) });
async function connect(s, index, p) { s.requests[index].resolve(p.response); await tick(); }

// Keep the page's authentication failure and completion wiring real as well:
// the events reader can revoke authentication while a POST stream is active.
function pageAuthHandlers(bindings) {
  const file = ts.createSourceFile('ServiceChatApp.tsx',
    readFileSync(resolve(__dirname, '../src/service-chat/ServiceChatApp.tsx'), 'utf8'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const page = file.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'ServiceChatApp');
  const auth = page.body.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'handleAuthFail');
  const declarations = page.body.statements.filter(ts.isVariableStatement)
    .flatMap(statement => [...statement.declarationList.declarations]);
  const stream = declarations.find(node => node.name.getText(file) === 'stream');
  const pageCallbacks = ['handleDeleteConversation', 'openConversation'].map(name => {
    const declaration = declarations.find(node => node.name.getText(file) === name);
    return `const ${name} = ${declaration.initializer.arguments[0].getText(file)};`;
  }).join('\n');
  const code = `${auth.getText(file)}\n${pageCallbacks}\nconst callbacks = ${stream.initializer.arguments[0].getText(file)};`;
  const compiled = ts.transpileModule(code, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  const context = { releasePendingSend: () => false, setSendError() {}, ...bindings };
  return new Function(...Object.keys(context), compiled + '\nreturn { handleAuthFail, handleDeleteConversation, openConversation, callbacks };')(...Object.values(context));
}

function pageSend(bindings) {
  const file = ts.createSourceFile('ServiceChatApp.tsx',
    readFileSync(resolve(__dirname, '../src/service-chat/ServiceChatApp.tsx'), 'utf8'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const page = file.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'ServiceChatApp');
  const declaration = page.body.statements.filter(ts.isVariableStatement)
    .flatMap(statement => [...statement.declarationList.declarations])
    .find(node => node.name.getText(file) === 'handleSend');
  const code = `const handleSend = ${declaration.initializer.arguments[0].getText(file)};`;
  const compiled = ts.transpileModule(code, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  return new Function(...Object.keys(bindings), compiled + '\nreturn handleSend;')(...Object.values(bindings));
}

test('first service message renders before creation and cannot be submitted twice', async () => {
  const creation = deferred(), response = deferred(), calls = [], messages = [];
  let draft = 'hello', creating = null;
  const pendingSend = { current: null }, conversationRequest = { current: 0 };
  const send = pageSend({
    apiKey: 'key', apiKeyRef: { current: 'key' }, byokCreds: null,
    convIdRef: { current: null }, pendingSend, nextLocalMessageId: { current: 0 }, conversationRequest,
    draft, pendingImgs: [], historyLoading: false, openingConversation: { current: null },
    stream: { isStreaming: false, send: (key, req) => { calls.push([key, req]); return response.promise; } },
    createAndRegister: () => { calls.push('create'); return creation.promise; },
    setMessages: update => { const next = update(messages); messages.splice(0, messages.length, ...next); },
    setDraft: value => { draft = value; }, setPendingImgs() {}, setWelcomeDismissed() {},
    setCreatingMessageId: value => { creating = value; }, setSendError() {},
    releasePendingSend() {}, handleAuthFail() {}, t: key => key, AuthError,
  });
  const first = send();
  assert.deepEqual(messages.map(m => m.data.text), ['hello']);
  assert.equal(creating, 'local-1'); assert.equal(draft, '');
  await send(); assert.deepEqual(calls, ['create']);
  creation.resolve('conversation-1'); await tick();
  assert.equal(creating, null); assert.equal(pendingSend.current.phase, 'streaming');
  assert.deepEqual(calls[1], ['key', { conversation_id: 'conversation-1', message: 'hello' }]);
  response.resolve(); await first;
  assert.equal(pendingSend.current, null);
});

test('failed first creation restores exact text and images for retry', async () => {
  const creation = deferred(), messages = [];
  let draft = 'look', images = [], error = '', creating = null;
  const original = [{ dataUrl: 'data:image/png;base64,AA', name: 'image.png' }];
  const pendingSend = { current: null };
  const releasePendingSend = () => {
    const pending = pendingSend.current; pendingSend.current = null; creating = null;
    if (pending?.phase !== 'creating') return false;
    draft = pending.text; images = pending.images; return true;
  };
  const send = pageSend({
    apiKey: 'key', apiKeyRef: { current: 'key' }, byokCreds: null,
    convIdRef: { current: null }, pendingSend, nextLocalMessageId: { current: 0 }, conversationRequest: { current: 0 },
    draft, pendingImgs: original, historyLoading: false, openingConversation: { current: null },
    stream: { isStreaming: false, send: () => assert.fail('must not stream after create failure') },
    createAndRegister: () => creation.promise,
    setMessages: update => { const next = update(messages); messages.splice(0, messages.length, ...next); },
    setDraft: value => { draft = value; }, setPendingImgs: value => { images = value; }, setWelcomeDismissed() {},
    setCreatingMessageId: value => { creating = value; }, setSendError: value => { error = value; },
    releasePendingSend, handleAuthFail() {}, t: (key, args) => `${key}: ${args?.status || ''}`, AuthError,
  });
  const first = send();
  assert.equal(messages.length, 1); assert.equal(messages[0].data.images.length, 1);
  creation.reject(new Error('offline')); await first;
  assert.deepEqual(messages, []); assert.equal(draft, 'look'); assert.deepEqual(images, original);
  assert.equal(creating, null); assert.equal(pendingSend.current, null);
  assert.match(error, /offline/);
});

test('navigation fences a late first-create response without overwriting the next chat', async () => {
  const creation = deferred(), messages = [];
  const pendingSend = { current: null }, conversationRequest = { current: 0 };
  let draft = 'old message', sent = 0;
  const send = pageSend({
    apiKey: 'key', apiKeyRef: { current: 'key' }, byokCreds: null,
    convIdRef: { current: null }, pendingSend, nextLocalMessageId: { current: 0 }, conversationRequest,
    draft, pendingImgs: [], historyLoading: false, openingConversation: { current: null },
    stream: { isStreaming: false, send: () => { sent++; } },
    createAndRegister: () => creation.promise,
    setMessages: update => { const next = update(messages); messages.splice(0, messages.length, ...next); },
    setDraft: value => { draft = value; }, setPendingImgs() {}, setWelcomeDismissed() {},
    setCreatingMessageId() {}, setSendError() {}, releasePendingSend() {}, handleAuthFail() {},
    t: key => key, AuthError,
  });
  const first = send();
  // A conversation switch restores the unsent draft and invalidates the old
  // create request before the network's late result is allowed to commit.
  draft = pendingSend.current.text;
  pendingSend.current = null;
  conversationRequest.current++;
  messages.splice(0, messages.length, { kind: 'assistant', data: { blocks: [] } });
  creation.resolve(null); await first;
  assert.equal(draft, 'old message'); assert.equal(sent, 0);
  assert.deepEqual(messages.map(m => m.kind), ['assistant']);
});

for (const late of ['token', 'done', 'error']) test(`events authentication failure fences old stream ${late} across reauthentication`, async () => {
  const s = mount(), a = pipe(), b = pipe();
  const stream = s.render();
  let messages = [], key = 'key-A';
  const convIdRef = { current: 'A' };
  const handlers = pageAuthHandlers({
    stream, convIdRef, config: { service_id: 'service' }, t: key => key,
    conversationRequest: { current: 1 }, openingConversation: { current: null }, didInitRef: { current: true },
    setHistoryLoading() {}, setApiKey(value) { key = value; }, clearStoredKey() {}, setAuthError() {},
    setConversationId(value) { convIdRef.current = value; }, setMediaToken() {}, setFilesPanelOpen() {}, setDrawerOpen() {},
    setMessages(value) { messages = typeof value === 'function' ? value(messages) : value; }, setConvList() {},
  });
  const old = s.render(handlers.callbacks).send(key, req('A')); await connect(s, 0, a);
  a.token('A partial'); await tick(); s.flush();

  // This callback is used by the independent GET events reader on 401/403.
  handlers.handleAuthFail('Service key revoked');
  assert.equal(key, ''); assert.equal(s.requests[0].signal.aborted, true);
  assert.equal(s.render().isStreaming, false); assert.deepEqual(s.render().blocks, []); assert.deepEqual(messages, []);

  // Reauthentication/history can finish before a buffered old read settles.
  convIdRef.current = 'B'; messages = [{ kind: 'assistant', data: { blocks: [{ type: 'text', content: 'B history' }] } }];
  const current = s.render(handlers.callbacks).send('key-B', req('B')); await connect(s, 1, b);
  b.token('B answer'); await tick(); s.flush();
  if (late === 'token') a.token('A buffered content');
  if (late === 'done') a.done();
  if (late === 'error') a.fail(new Error('A disconnected late'));
  await old;
  assert.equal(s.render().isStreaming, true); assert.equal(text(s.render().blocks), 'B answer');
  assert.deepEqual(messages.map(m => text(m.data.blocks)), ['B history']);
  assert.equal(s.requests[1].signal.aborted, false);
  b.done(); await current;
  assert.deepEqual(messages.map(m => text(m.data.blocks)), ['B history', 'B answer']);
  assert.equal(s.render().isStreaming, false);
});

for (const pending of ['fetch', 'reader']) test(`deleting the active conversation invalidates its pending ${pending}`, async () => {
  const s = mount(), a = pipe(), b = pipe();
  const stream = s.render(), convIdRef = { current: 'A' };
  let messages = [], conversations = [{ id: 'A' }, { id: 'B' }];
  const handlers = pageAuthHandlers({
    stream, convIdRef, t: key => key,
    conversationRequest: { current: 1 }, openingConversation: { current: null },
    setHistoryLoading() {}, setConversationId(value) { convIdRef.current = value; }, setMediaToken() {}, setWelcomeDismissed() {},
    setMessages(value) { messages = typeof value === 'function' ? value(messages) : value; },
    setConvList(value) { conversations = value(conversations); },
  });
  const old = s.render(handlers.callbacks).send('key', req('A'));
  if (pending === 'reader') { await connect(s, 0, a); a.token('A partial'); await tick(); s.flush(); }
  handlers.handleDeleteConversation('A');
  assert.equal(s.requests[0].signal.aborted, true); assert.equal(s.render().isStreaming, false);
  assert.equal(convIdRef.current, null); assert.deepEqual(messages, []); assert.deepEqual(conversations, [{ id: 'B' }]);
  convIdRef.current = 'B';
  const current = s.render(handlers.callbacks).send('key', req('B')); await connect(s, 1, b);
  b.token('B answer'); await tick();
  if (pending === 'fetch') s.requests[0].resolve(a.response); else a.done();
  await old;
  assert.equal(s.render().isStreaming, true); assert.equal(s.requests[1].signal.aborted, false); assert.deepEqual(messages, []);
  b.done(); await current;
  assert.deepEqual(messages.map(m => text(m.data.blocks)), ['B answer']);
});

test('deleting a pending history target fences its response and resumes the current event reader', async () => {
  const history = deferred(), convIdRef = { current: 'A' }, openingConversation = { current: null };
  const conversationRequest = { current: 1 };
  let messages = ['A history'], conversations = [{ id: 'A' }, { id: 'B' }], loading = false, historyEpoch = 0, mediaCalls = 0;
  const handlers = pageAuthHandlers({
    stream: {}, convIdRef, openingConversation, conversationRequest, t: key => key, AuthError,
    getConversation: () => history.promise, refreshMediaToken() { mediaCalls++; }, backendMsgToEntry: value => value,
    setHistoryLoading(value) { loading = value; }, setHistoryEpoch(value) { historyEpoch = value(historyEpoch); },
    setConversationId(value) { convIdRef.current = value; }, setWelcomeDismissed() {},
    setMessages(value) { messages = typeof value === 'function' ? value(messages) : value; },
    setConvList(value) { conversations = value(conversations); },
  });
  const pending = handlers.openConversation('B', 'key');
  assert.equal(loading, true); assert.equal(openingConversation.current, 'B');
  handlers.handleDeleteConversation('B');
  assert.equal(loading, false); assert.equal(openingConversation.current, null); assert.equal(historyEpoch, 1);
  history.resolve({ id: 'B', messages: ['stale B history'] }); await pending;
  assert.equal(convIdRef.current, 'A'); assert.deepEqual(messages, ['A history']);
  assert.deepEqual(conversations, [{ id: 'A' }]); assert.equal(historyEpoch, 1); assert.equal(mediaCalls, 0);
});

for (const late of ['response', 'http', 'error', 'auth']) test(`late old fetch ${late} cannot affect new stream`, async () => {
  const s = mount(), log = [];
  const old = s.render(callbacks('A', log)).send('key', req('A'));
  const current = s.render(callbacks('B', log)).send('key', req('B'));
  assert.equal(s.requests[0].signal.aborted, true);
  const b = pipe(); await connect(s, 1, b); b.token('B only'); await tick(); s.flush();
  const a = pipe();
  if (late === 'response') s.requests[0].resolve(a.response);
  if (late === 'http') s.requests[0].resolve({ ok: false, status: 500, body: null });
  if (late === 'error') s.requests[0].reject(new Error('old offline'));
  if (late === 'auth') s.requests[0].reject(new AuthError('old key'));
  await old;
  assert.equal(s.render().isStreaming, true); assert.equal(text(s.render().blocks), 'B only');
  assert.deepEqual(log, []); assert.equal(s.requests[1].signal.aborted, false);
  if (late === 'response') { assert.equal(a.opened, 0); assert.equal(a.cancelled, 1); }
  b.done(); await current;
  assert.deepEqual(log, [['B', 'done', 'B only']]); assert.equal(s.render().isStreaming, false);
});

for (const late of ['token', 'done', 'error', 'auth']) test(`late old reader ${late} cannot mutate or finalize B`, async () => {
  const s = mount(), log = [], a = pipe(), b = pipe();
  const old = s.render(callbacks('A', log)).send('key', req('A')); await connect(s, 0, a);
  a.token('A partial'); await tick(); s.flush();
  const current = s.render(callbacks('B', log)).send('key', req('B')); await connect(s, 1, b);
  b.token('B only'); await tick(); s.flush();
  if (late === 'token') a.token('old buffered content');
  if (late === 'done') a.done();
  if (late === 'error') a.fail(new Error('old read failure'));
  if (late === 'auth') a.fail(new AuthError('old authentication failure'));
  await old;
  assert.equal(a.released, 1); assert.equal(s.render().isStreaming, true);
  assert.equal(text(s.render().blocks), 'B only'); assert.deepEqual(log, []);
  b.done(); await current; assert.deepEqual(log, [['B', 'done', 'B only']]);
});

test('stale finally cannot remove the controller needed to abort the new stream', async () => {
  const s = mount(), log = [];
  const old = s.render(callbacks('A', log)).send('key', req('A'));
  const current = s.render(callbacks('B', log)).send('key', req('B'));
  s.requests[0].reject(new Error('A ended late')); await old;
  s.render().abort(); assert.equal(s.requests[1].signal.aborted, true);
  s.requests[1].resolve({ ok: false, status: 503, body: null }); await current;
  assert.deepEqual(log, []); assert.equal(s.render().isStreaming, false);
});

for (const action of ['abort', 'reset']) test(`${action} alone invalidates late reader completion`, async () => {
  const s = mount(), log = [], a = pipe();
  const old = s.render(callbacks('A', log)).send('key', req('A')); await connect(s, 0, a);
  a.token('A partial'); await tick(); s.flush(); s.render()[action]();
  assert.equal(s.requests[0].signal.aborted, true); a.token('should not append'); await old;
  assert.equal(s.render().isStreaming, false);
  assert.equal(text(s.render().blocks), action === 'reset' ? '' : 'A partial'); assert.deepEqual(log, []);
});

test('completion uses callbacks captured at send, not a later render', async () => {
  const s = mount(), log = [], a = pipe();
  const run = s.render(callbacks('A', log)).send('key', req('A')); await connect(s, 0, a);
  s.render(callbacks('B', log)); a.token('belongs to A'); await tick(); a.done(); await run;
  assert.deepEqual(log, [['A', 'done', 'belongs to A']]);
});

test('onDone may start a new run synchronously without old finally stopping it', async () => {
  const s = mount(), log = [], a = pipe(), b = pipe(); let next;
  const old = s.render({ onDone: blocks => { log.push(['A', text(blocks)]);
    next = s.render(callbacks('B', log)).send('key', req('B')); } }).send('key', req('A'));
  await connect(s, 0, a); a.token('A answer'); await tick(); a.done(); await old;
  assert.equal(s.render().isStreaming, true);
  await connect(s, 1, b); b.token('B answer'); await tick(); b.done(); await next;
  assert.deepEqual(log, [['A', 'A answer'], ['B', 'done', 'B answer']]);
});

test('active reader failure commits partial content and reports error once', async () => {
  const s = mount(), log = [], a = pipe();
  const run = s.render(callbacks('A', log)).send('key', req('A')); await connect(s, 0, a);
  a.token('partial answer'); await tick(); a.fail(new Error('offline')); await run;
  assert.equal(log.length, 2); assert.equal(log[0][0], 'A'); assert.equal(log[0][1], 'done');
  assert.match(log[0][2], /partial answer.*连接中断/s); assert.deepEqual(log[1], ['A', 'error', 'offline']);
  assert.equal(s.render().isStreaming, false);
});

test('cancelled RAF from A cannot flush B or clear its pending frame', async () => {
  const s = mount(), log = [], a = pipe(), b = pipe();
  const old = s.render(callbacks('A', log)).send('key', req('A')); await connect(s, 0, a);
  a.token('A'); await tick(); const staleFrame = [...s.frames.values()][0];
  const current = s.render(callbacks('B', log)).send('key', req('B')); await connect(s, 1, b);
  b.token('B'); await tick(); staleFrame();
  assert.equal(text(s.render().blocks), ''); assert.equal(s.frames.size, 1);
  s.flush(); assert.equal(text(s.render().blocks), 'B'); a.done(); await old; b.done(); await current;
});

for (const pending of ['fetch', 'reader']) test(`unmount invalidates late ${pending} work and callbacks`, async () => {
  const s = mount(), log = [], a = pipe();
  const run = s.render(callbacks('A', log)).send('key', req('A'));
  if (pending === 'reader') await connect(s, 0, a);
  s.unmount(); assert.equal(s.requests[0].signal.aborted, true);
  if (pending === 'reader') a.token('late after unmount'); else s.requests[0].resolve(a.response);
  await run; assert.equal(s.writesAfterUnmount, 0); assert.deepEqual(log, []);
});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const ts = require('typescript');
function component(name) {
  const source = readFileSync(resolve(__dirname, '../src/components', `${name}.tsx`), 'utf8');
  const file = ts.createSourceFile(`${name}.tsx`, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  return { file, body: file.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === name).body };
}
function bind(code, values) {
  const compiled = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  return new Function(...Object.keys(values), compiled)(...Object.values(values));
}
function effect(name, match, values) {
  const { file, body } = component(name);
  const call = body.statements.find(n => ts.isExpressionStatement(n) && ts.isCallExpression(n.expression)
    && n.expression.expression.getText(file) === 'useEffect' && match(n.expression.arguments[0].getText(file)));
  assert(call, 'production effect exists');
  return bind(`return (${call.expression.arguments[0].getText(file)});`, values);
}
function action(values) {
  const { file, body } = component('RuntimeGrants');
  const declaration = body.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'act');
  return bind(`${declaration.getText(file)}\nreturn act;`, values);
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
// Advance real production polling across several periods without resolving the HTTP request.
function clock() {
  let now = 0, id = 0;
  const tasks = new Map();
  const add = (fn, ms, interval) => { const key = ++id; tasks.set(key, { fn, at: now + ms, interval }); return key; };
  return {
    setTimeout: (fn, ms) => add(fn, ms, 0), clearTimeout: key => tasks.delete(key),
    setInterval: (fn, ms) => add(fn, ms, ms), clearInterval: key => tasks.delete(key),
    advance(ms) {
      const end = now + ms;
      while (true) {
        const next = [...tasks.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        const [key, task] = next; now = task.at;
        if (task.interval) task.at += task.interval; else tasks.delete(key);
        void task.fn();
      }
      now = end;
    },
    count: () => tasks.size,
  };
}
test('a slow profile poll still updates and never overlaps another poll', async () => {
  const timer = clock(), pending = deferred(), updates = []; let requests = 0;
  const cleanup = effect('RuntimeSettingsCard', code => code.includes('api.profiles()'), {
    ...timer, caps: { available: true }, loading: false, busy: false, readVersion: { current: 0 },
    api: { profiles: () => { requests++; return pending.promise; } },
    setProfiles: v => updates.push(v), setProfilesLoaded: () => {},
  })();
  timer.advance(12000);
  assert.equal(requests, 1, 'a response delayed across three polling periods still has only one request');
  pending.resolve([{ id: 'ready-profile', status: 'ready' }]); await settle();
  assert.deepEqual(updates, [[{ id: 'ready-profile', status: 'ready' }]]);
  assert.equal(timer.count(), 1);
  cleanup(); timer.advance(3000); assert.equal(requests, 1);
});
test('late profile responses cannot update a disposed polling effect', async () => {
  const timer = clock(), pending = deferred(), updates = [];
  const cleanup = effect('RuntimeSettingsCard', code => code.includes('api.profiles()'), {
    ...timer, caps: { available: true }, loading: false, busy: false, readVersion: { current: 0 },
    api: { profiles: () => pending.promise }, setProfiles: v => updates.push(v), setProfilesLoaded: () => {},
  })();
  timer.advance(3000); cleanup(); pending.resolve([{ id: 'stale' }]); await settle();
  assert.deepEqual(updates, []); assert.equal(timer.count(), 0);
});
function grantContext(api) {
  const state = { grants: [], admins: [], loaded: true, loading: false, busy: false, saved: false, error: '' };
  const profile = { id: 'profile-a', auth_generation: 1 };
  const values = {
    api, profile, state, mounted: { current: true }, actionPending: { current: false }, readVersion: { current: 0 },
    scope: { current: { profileId: profile.id, authGeneration: profile.auth_generation, api } }, text: (_zh, en) => en,
  };
  for (const name of Object.keys(state)) values[`set${name[0].toUpperCase()}${name.slice(1)}`] = v => { state[name] = v; };
  return values;
}
test('manual refresh during an authorization write cannot hide the committed grant', async () => {
  const post = deferred(), oldRead = deferred(); let reads = 0, writes = 0;
  const newGrant = { id: 'grant-new', models: ['model-a'] };
  const api = { admins: async () => [{ id: 'admin-a' }], grants: () => ++reads === 1 ? oldRead.promise : Promise.resolve([newGrant]) };
  const context = grantContext(api), act = action(context);
  const receipt = act(() => { writes++; return post.promise; });
  const cleanup = effect('RuntimeGrants', code => code.includes('Promise.all'), context)();
  await act(() => { writes++; return Promise.resolve(); }); assert.equal(writes, 1);
  post.resolve(); await receipt;
  assert.deepEqual(context.state.grants, [newGrant]); assert.equal(context.state.loaded, true);
  assert.equal(context.state.loading, false); assert.equal(context.state.saved, true);
  oldRead.resolve([]); await settle();
  assert.deepEqual(context.state.grants, [newGrant], 'an earlier snapshot cannot replace the committed result'); cleanup();
});
test('a stale refresh error cannot cover a successful authorization receipt', async () => {
  const post = deferred(), oldRead = deferred(); let reads = 0;
  const api = { admins: async () => [{ id: 'admin-a' }], grants: () => ++reads === 1 ? oldRead.promise : Promise.resolve([{ id: 'new' }]) };
  const context = grantContext(api), receipt = action(context)(() => post.promise);
  const cleanup = effect('RuntimeGrants', code => code.includes('Promise.all'), context)();
  post.resolve(); await receipt; oldRead.reject(new Error('old snapshot failed')); await settle();
  assert.equal(context.state.error, ''); assert.equal(context.state.saved, true);
  assert.deepEqual(context.state.grants, [{ id: 'new' }]); cleanup();
});
test('receipts from an old provider account cannot update a new account view', async () => {
  for (const change of ['profile', 'account', 'api', 'unmount']) {
    const post = deferred(); let reads = 0;
    const api = { admins: async () => [], grants: async () => { reads++; return [{ id: 'old' }]; } };
    const context = grantContext(api), receipt = action(context)(() => post.promise);
    if (change === 'profile') context.scope.current = { ...context.scope.current, profileId: 'profile-b' };
    if (change === 'account') context.scope.current = { ...context.scope.current, authGeneration: 2 };
    if (change === 'api') context.scope.current = { ...context.scope.current, api: {} };
    if (change === 'unmount') context.mounted.current = false;
    post.resolve(); await receipt;
    assert.equal(reads, 0, change); assert.deepEqual(context.state.grants, [], change); assert.equal(context.state.saved, false, change);
  }
});
test('failed member loading is not mistaken for an empty successful list', async () => {
  const api = { admins: async () => { throw new Error('members unavailable'); }, grants: async () => [] };
  const context = grantContext(api);
  const cleanup = effect('RuntimeGrants', code => code.includes('Promise.all'), context)(); await settle();
  assert.equal(context.state.loaded, false); assert.equal(context.state.loading, false);
  assert.equal(context.state.error, 'members unavailable'); cleanup();
});

test('an account change during the receipt refresh discards the old account snapshot', async () => {
  const read = deferred();
  const api = { admins: async () => [{ id: 'old-admin' }], grants: () => read.promise };
  const context = grantContext(api), receipt = action(context)(async () => {});
  await settle();
  context.scope.current = { ...context.scope.current, authGeneration: 2 };
  read.resolve([{ id: 'old-grant' }]); await receipt;
  assert.deepEqual(context.state.grants, []);
  assert.deepEqual(context.state.admins, []);
  assert.equal(context.state.saved, false);
});

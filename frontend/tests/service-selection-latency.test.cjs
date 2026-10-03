const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const ts = require('typescript');

const file = ts.createSourceFile('AdminServicesPage.tsx',
  readFileSync(resolve(__dirname, '../src/pages/AdminServices/index.tsx'), 'utf8'),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const page = file.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'AdminServicesPage');
const declarations = page.body.statements.filter(ts.isVariableStatement)
  .flatMap(statement => [...statement.declarationList.declarations]);

function actualHandler(name, bindings) {
  const declaration = declarations.find(node => node.name.getText(file) === name);
  assert(declaration, `Missing ${name}`);
  const initializer = declaration.initializer;
  const expression = ts.isCallExpression(initializer) && initializer.expression.getText(file) === 'useCallback'
    ? initializer.arguments[0] : initializer;
  const code = ts.transpileModule(`const handler = ${expression.getText(file)};`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return new Function(...Object.keys(bindings), code + '\nreturn handler;')(...Object.values(bindings));
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('old service Key, conversation and usage responses cannot overwrite the new service', async () => {
  const selectedService = { current: { id: 'A', generation: 1 } };
  const detailRequests = { current: { keys: 0, wc: 0, convs: 0, usage: 0, tokens: 0, drawer: 0, chat: 0 } };
  const beginDetailRequest = actualHandler('beginDetailRequest', { selectedService, detailRequests });
  const keys = [], convs = [], usage = [];
  const pending = { keys: [], convs: [], usage: [] };
  const state = { keys: [], convs: [], usage: [], keysLoading: false, convsLoading: false, usageLoading: false };
  const errors = [];
  const loadKeys = actualHandler('loadKeys', {
    selectedService, beginDetailRequest,
    setKeysLoading: value => { state.keysLoading = value; },
    listServiceKeys: sid => { keys.push(sid); const d = deferred(); pending.keys.push(d); return d.promise; },
    setServiceKeys: value => { state.keys = value; }, message: { error: value => errors.push(value) },
  });
  const loadSvcConvs = actualHandler('loadSvcConvs', {
    selectedService, beginDetailRequest,
    setSvcConvsLoading: value => { state.convsLoading = value; },
    listServiceConversations: sid => { convs.push(sid); const d = deferred(); pending.convs.push(d); return d.promise; },
    setSvcConvs: value => { state.convs = value; },
  });
  const loadSvcUsage = actualHandler('loadSvcUsage', {
    selectedService, beginDetailRequest,
    setSvcUsageLoading: value => { state.usageLoading = value; },
    listServiceUsage: (sid, options) => { usage.push([sid, options.channel]); const d = deferred(); pending.usage.push(d); return d.promise; },
    setSvcUsage: value => { state.usage = value; },
  });

  const old = [loadKeys('A'), loadSvcConvs('A'), loadSvcUsage('A')];
  selectedService.current = { id: 'B', generation: 2 };
  state.keys = []; state.convs = []; state.usage = [];
  const current = [loadKeys('B'), loadSvcConvs('B'), loadSvcUsage('B')];
  pending.keys[0].reject(new Error('old Key failure'));
  pending.convs[0].resolve([{ id: 'A-conv' }]);
  pending.usage[0].resolve({ records: [{ id: 'A-usage' }] });
  await Promise.all(old);
  assert.deepEqual([state.keys, state.convs, state.usage], [[], [], []]);
  assert.deepEqual(errors, []);
  assert.equal(state.keysLoading, true); assert.equal(state.convsLoading, true); assert.equal(state.usageLoading, true);
  pending.keys[1].resolve([{ id: 'B-key' }]);
  pending.convs[1].resolve([{ id: 'B-conv' }]);
  pending.usage[1].resolve({ records: [{ id: 'B-usage' }] });
  await Promise.all(current);
  assert.deepEqual(state.keys, [{ id: 'B-key' }]);
  assert.deepEqual(state.convs, [{ id: 'B-conv' }]);
  assert.deepEqual(state.usage, [{ id: 'B-usage' }]);
  assert.deepEqual([state.keysLoading, state.convsLoading, state.usageLoading], [false, false, false]);
  assert.deepEqual(keys, ['A', 'B']); assert.deepEqual(convs, ['A', 'B']);
  assert.deepEqual(usage, [['A', undefined], ['B', undefined]]);
});

test('latest filter or month request wins within the same service', async () => {
  const selectedService = { current: { id: 'B', generation: 3 } };
  const detailRequests = { current: { keys: 0, wc: 0, convs: 0, usage: 0, tokens: 0, drawer: 0, chat: 0 } };
  const beginDetailRequest = actualHandler('beginDetailRequest', { selectedService, detailRequests });
  const usageRequests = [], tokenRequests = [];
  let usage = [], tokens = null, usageLoading = false, tokenLoading = false;
  const loadSvcUsage = actualHandler('loadSvcUsage', {
    selectedService, beginDetailRequest,
    setSvcUsageLoading: value => { usageLoading = value; },
    listServiceUsage: () => { const d = deferred(); usageRequests.push(d); return d.promise; },
    setSvcUsage: value => { usage = value; },
  });
  const loadSvcTokenUsage = actualHandler('loadSvcTokenUsage', {
    selectedService, beginDetailRequest,
    setSvcTokenLoading: value => { tokenLoading = value; },
    getServiceTokenUsage: () => { const d = deferred(); tokenRequests.push(d); return d.promise; },
    setSvcTokenUsage: value => { tokens = value; },
  });
  const old = [loadSvcUsage('B', 'web'), loadSvcTokenUsage('B', 3)];
  const current = [loadSvcUsage('B', 'api'), loadSvcTokenUsage('B', 6)];
  usageRequests[0].resolve({ records: [{ id: 'old' }] }); tokenRequests[0].resolve({ months: 3 });
  await Promise.all(old);
  assert.deepEqual(usage, []); assert.equal(tokens, null);
  assert.equal(usageLoading, true); assert.equal(tokenLoading, true);
  usageRequests[1].resolve({ records: [{ id: 'new' }] }); tokenRequests[1].resolve({ months: 6 });
  await Promise.all(current);
  assert.deepEqual(usage, [{ id: 'new' }]); assert.deepEqual(tokens, { months: 6 });
  assert.equal(usageLoading, false); assert.equal(tokenLoading, false);
});

test('a late generated Key remains scoped to its own service', async () => {
  const selectedService = { current: { id: 'A', generation: 1 } };
  const keyGeneratingRef = { current: new Set() };
  let generatedKeys = {}, generatingIds = [];
  const creation = deferred();
  const generate = actualHandler('handleGenerateKey', {
    currentSvc: { id: 'A' }, selectedService, keyGeneratingRef,
    keyName: 'test', keyBilling: 'hosted',
    setKeyGeneratingIds: update => { generatingIds = update(generatingIds); },
    createServiceKey: () => creation.promise,
    setGeneratedKeys: update => { generatedKeys = update(generatedKeys); },
    message: { error: () => assert.fail('unexpected error') },
  });
  const pending = generate();
  selectedService.current = { id: 'B', generation: 2 };
  creation.resolve({ key: 'A-only-secret' }); await pending;
  assert.equal(generatedKeys.B, undefined);
  assert.deepEqual(generatedKeys.A, { value: 'A-only-secret', billing: 'hosted' });
  assert.deepEqual(generatingIds, []);
});

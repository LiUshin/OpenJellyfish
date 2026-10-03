const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

// Run the exact public page script against a controlled DOM and fetch boundary.
async function publicPage() {
  const html = fs.readFileSync(path.join(root, 'public/wechat-scan.html'), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const nodes = {}, timers = new Map(); let timer = 0, pollResult = response(200, { status: 'waiting' });
  const state = {
    location: { origin: 'http://fixture' },
    document: { getElementById(id) { return nodes[id] ||= { textContent: '', innerHTML: '', style: {} }; } },
    fetch: async url => url.includes('/status?') ? pollResult : response(200, { qr_id: 'fixture-qr', qr_image_b64: 'fixture' }),
    setInterval: (callback, delay) => { timers.set(++timer, { callback, delay }); return timer; },
    clearInterval: id => timers.delete(id), Date, encodeURIComponent,
  };
  state.window = state;
  vm.runInNewContext(script, state);
  await tick();
  return { nodes, timers, reload: state.loadQR, setResult: value => { pollResult = value; },
    poll: () => [...timers.values()].find(item => item.delay === 2000).callback() };
}

for (const status of [403, 404, 410]) test(`public QR ${status} stops timers, hides stale code, and offers retry`, async () => {
  const page = await publicPage();
  page.setResult(response(status, { detail: 'fixture terminal error' }));
  await page.poll();
  assert.equal(page.timers.size, 0);
  assert.equal(page.nodes['retry-btn'].style.display, 'block');
  assert.equal(page.nodes['qr-img'].style.display, 'none');
  assert.equal(page.nodes['scan-hint'].textContent, 'fixture terminal error');
});
test('public QR transient outage continues polling and stale failure cannot invalidate new QR', async () => {
  const page = await publicPage();
  page.setResult(response(503, {})); await page.poll();
  assert.equal(page.timers.size, 2);
  assert.match(page.nodes['scan-hint'].textContent, /重试/);
  const pending = deferred(); page.setResult(pending.promise);
  const old = page.poll(); await page.reload();
  pending.resolve(response(410, { detail: 'stale' })); await old;
  assert.equal(page.nodes['retry-btn'].style.display, 'none');
  assert.equal(page.nodes['qr-img'].style.display, 'block');
  assert.equal(page.timers.size, 2);
});

// Extract the production admin callback; do not duplicate its status logic.
function adminPage(request) {
  const text = fs.readFileSync(path.join(root, 'src/pages/WeChat/index.tsx'), 'utf8');
  const source = ts.createSourceFile('WeChat.tsx', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const page = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'WeChatPage');
  const declaration = page.body.statements.filter(ts.isVariableStatement)
    .flatMap(node => [...node.declarationList.declarations]).find(node => node.name.getText(source) === 'startPolling');
  const code = ts.transpileModule(`const startPolling = ${declaration.initializer.getText(source)};`,
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  let active, stopped = 0, error = '', status = 'waiting';
  const generation = { current: 0 };
  const bindings = {
    stopPolling() { ++generation.current; ++stopped; active = null; },
    qrGenerationRef: generation, pollRef: { current: null }, POLL_INTERVAL: 2500,
    setInterval(callback) { active = callback; return 1; },
    api: { request }, setQrError: value => { error = value; }, setQrStatus: value => { status = value; },
    checkSession: async () => {},
  };
  const start = new Function(...Object.keys(bindings), code + '; return startPolling;')(...Object.values(bindings));
  return { start, poll: () => active(), state: () => ({ active, stopped, error, status }) };
}
for (const status of [403, 404, 410]) test(`admin QR ${status} exits spinner with visible retry error`, async () => {
  const page = adminPage(async () => { throw Object.assign(new Error('fixture terminal error'), { status }); });
  page.start('qr'); await page.poll();
  assert.equal(page.state().active, null);
  assert.equal(page.state().status, 'expired');
  assert.equal(page.state().error, 'fixture terminal error');
});
test('admin QR transient failures retry, and a stale confirmation never ends a newer poll', async () => {
  const pending = deferred(); let current = 'offline';
  const page = adminPage(async () => {
    if (current === 'offline') throw new TypeError('network');
    return pending.promise;
  });
  page.start('one'); await page.poll();
  assert.equal(typeof page.state().active, 'function');
  assert.match(page.state().error, /重试/);
  current = 'pending'; const old = page.poll();
  page.start('two'); pending.resolve({ status: 'confirmed' }); await old;
  assert.equal(typeof page.state().active, 'function');
  assert.equal(page.state().status, 'waiting');
});

const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'dist/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].split('// ── Init')[0];

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.value = ''; this.textContent = ''; this.style = {};
    this.attributes = {}; this.listeners = {}; this.hidden = false; this.disabled = false; this.checked = false;
    this.classes = new Set();
    this.classList = {contains: c => this.classes.has(c), add: c => this.classes.add(c), remove: c => this.classes.delete(c), toggle: (c, force) => { const on = force ?? !this.classes.has(c); on ? this.classes.add(c) : this.classes.delete(c); return on; }};
  }
  set innerHTML(value) { this.html = value; this.children = []; }
  get innerHTML() { return this.html ?? String(this.textContent).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  get options() { return this.children; }
  appendChild(el) { this.children.push(el); return el; }
  replaceChildren(...els) { this.children = els; }
  addEventListener(event, fn) { this.listeners[event] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  focus() {}
  select() {}
  checkValidity() { return true; }
}
function setup(handler = async () => undefined) {
  const elements = new Map(); const intervals = [];
  const el = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const document = {
    getElementById: el, createElement: tag => new Element(tag),
    querySelectorAll: () => [], querySelector: () => null, addEventListener() {},
  };
  const context = vm.createContext({
    window: {__TAURI__: {core: {invoke: handler}}, addEventListener() {}},
    document, location: {search:''}, URLSearchParams, navigator: {clipboard: {writeText: async () => {}}},
    console, setTimeout: () => 0, clearTimeout() {}, setInterval: fn => { intervals.push(fn); return intervals.length; }, clearInterval() {},
  });
  vm.runInContext(script, context);
  return {context, el, intervals, run: code => vm.runInContext(code, context)};
}

test('account names are data, including quotes and script-looking text', async () => {
  const hostile = `O'Connor\");window.__injected=true;//<img src=x onerror=alert(1)>`;
  const app = setup(async command => command === 'list_admin_users' ? [{user_id:'demo',username:hostile,has_api_keys:true}] : undefined);
  await app.run('loadAdminUsers()');
  const row = app.el('adminTableBody').children[0];
  assert.equal(row.children[0].textContent, hostile);
  let received;
  app.context.showRenameUser = (...args) => { received = args; };
  row.children[5].children[0].listeners.click();
  assert.deepEqual(received, ['demo', hostile]);
  function verifyTree(node) {
    assert.equal(node.attributes.onclick, undefined);
    assert.notEqual(node.tagName, 'img');
    node.children.forEach(verifyTree);
  }
  verifyTree(row);
  assert.equal(app.context.window.__injected, undefined);
});

test('failed config write blocks service start and allows retry', async () => {
  const calls=[];
  const app=setup(async command => {calls.push(command);if(command==='save_env_config')throw Error('disk full');});
  app.run('environmentReady=true; configLoaded=true; configDirty=true; lastStatus={running:false};');
  await app.run('toggleApp()');
  assert.deepEqual(calls,['save_env_config']);
  assert.equal(app.run('configDirty'),true);
  assert.equal(app.el('powerBtn').disabled,false);
});

test('starting with untouched config does not rewrite defaults or existing settings', async () => {
  const calls=[];
  const app=setup(async command => {calls.push(command);return {running:true,backend_ready:false,frontend_ready:false};});
  app.run('environmentReady=true; configLoaded=true;');
  await app.run('toggleApp()');
  assert.deepEqual(calls,['start_jellyfish']);
  assert.equal(app.el('powerBtn').disabled,false,'starting remains cancellable');
  assert.equal(app.el('powerLabel').textContent,'停止服务');
});

test('stop errors retain confirmed running state instead of reporting stopped', async () => {
  const app=setup(async command => {if(command==='stop_jellyfish')throw Error('stop failed'); return {running:true,backend_ready:true,frontend_ready:true};});
  app.run('isRunning=true;');
  await app.run('toggleApp()');
  assert.equal(app.run('isRunning'),true);
  assert.equal(app.el('runBadge').textContent,'运行中');
});

test('refresh restores running state; idle updates cannot bypass environment failure', async () => {
  const app=setup(async () => ({running:true,backend_ready:true,frontend_ready:true}));
  await app.run('restoreRunStatus()');
  assert.equal(app.run('isRunning'),true);
  assert.equal(app.intervals.length,1);
  app.run('environmentReady=false; configLoaded=true; updateRunUI({running:false});');
  assert.equal(app.el('powerBtn').disabled,true);
});

test('old in-flight polling cannot resurrect a stopped service', async () => {
  let resolve;
  const app=setup(() => new Promise(r => {resolve=r;}));
  app.run('startPolling()');
  const pending=app.intervals[0]();
  app.run('stopPolling(); updateRunUI({running:false});');
  resolve({running:true,backend_ready:true,frontend_ready:true});
  await pending;
  assert.equal(app.run('isRunning'),false);
});

test('unsupported stored backend stays preserved when loading and saving unrelated config', async () => {
  let saved;
  const app=setup(async (command,args) => {
    if(command==='load_env_config')return {JELLYFISH_RUNTIME_BACKEND:'docker'};
    if(command==='save_env_config')saved=args.config;
  });
  app.run('_configRendered=true;');
  await app.run('loadConfig()');
  assert.equal(app.el('cfg_JELLYFISH_RUNTIME_BACKEND').value,'docker');
  assert.equal(app.el('cfg_JELLYFISH_RUNTIME_BACKEND').options[0].value,'docker');
  await app.run('saveConfig()');
  assert.equal(saved.JELLYFISH_RUNTIME_BACKEND,'docker');
});

test('late key read cannot repopulate credentials after clearing', async () => {
  let resolve;
  const app=setup(() => new Promise(r => {resolve=r;}));
  app.el('page-connections').classList.add('active');
  const pending=app.run('showHostKey()');
  app.run('clearHostKey()');
  resolve('fixture-not-a-real-credential');
  await pending;
  assert.equal(app.el('hostKey').value,'');
  assert.equal(app.el('hostKeyPanel').hidden,true);
});

test('UI settings remain paired with the native writable allowlist', () => {
  const app=setup();
  const keys=Array.from(app.run('CONFIG_SCHEMA.flatMap(group => group.fields.map(f => f.key))')).sort();
  const native=fs.readFileSync(path.join(root,'src-tauri/src/lib.rs'),'utf8').match(/const KNOWN_ENV_KEYS: &[\s\S]*?= &\[([\s\S]*?)\];/)[1];
  const nativeKeys=Array.from(native.matchAll(/"([A-Z0-9_]+)"/g),m=>m[1]).sort();
  assert.deepEqual(keys,nativeKeys);
});

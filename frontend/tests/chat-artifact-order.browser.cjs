const assert = require('node:assert/strict');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:3004';
const html = `<!doctype html><html data-color="dark" data-style="terminal"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module">
import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>(type)=>type; window.__vite_plugin_react_preamble_installed__=true;
const React=(await import('/node_modules/.vite/deps/react.js')).default;
const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
const {App}=await import('/node_modules/.vite/deps/antd.js');
await import('/src/styles/global.css'); await import('/src/i18n/index.ts');
const {FileWorkspaceProvider}=await import('/src/stores/fileWorkspaceContext.tsx');
const {default:RuntimeConversation}=await import('/src/pages/Chat/components/RuntimeConversation.tsx');
const {default:FilePreview}=await import('/src/components/FilePreview.tsx');
const h=React.createElement;
createRoot(document.getElementById('root')).render(h(App,null,h(FileWorkspaceProvider,null,
  h('main',{style:{display:'flex',height:'100vh',maxWidth:1280,margin:'auto'}},
    h('section',{style:{position:'relative',display:'flex',flexDirection:'column',flex:1,minWidth:0}},h(RuntimeConversation,{sid:'session',conversationId:'conv',history:[],profiles:[],onChanged:()=>{}})),
    h('section',{style:{width:400,display:'flex',flexDirection:'column'}},h(FilePreview))
  )
)));
</script></body></html>`;

(async () => {
 const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
 try {
  for (const engine of ['codex', 'cursor']) {
   const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
   await context.addInitScript(() => {
    localStorage.setItem('token', 'fixture-token');
    localStorage.setItem('jf-split-mode', 'split');
    localStorage.setItem('i18nextLng', 'zh');
    // Feed frames incrementally through the real watchRun SSE reader. Only the
    // transport is mocked; React, event projection, terminal refresh and storage
    // preview all use the production frontend implementations.
    const fetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
     if (String(input).includes('/api/runtime/runs/run/events')) {
      return Promise.resolve(new Response(new ReadableStream({
       start(controller) { window.runtimeController = controller; },
      }), { headers: { 'Content-Type': 'text/event-stream' } }));
     }
     return fetch(input, init);
    };
    window.sendRuntimeEvent = event => window.runtimeController.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
   });
   const report = { id: 'report', name: 'report.md', path: '/generated/runtime/session/run/report.md', mime: 'text/markdown', size: 32 };
   const notes = { id: 'notes', name: 'notes.txt', path: '/generated/runtime/session/run/notes.txt', mime: 'text/plain', size: 16 };
   const body = `说明在前\n\n<<FILE:${report.path}>>\n\n总结在后`;
   const binding = { runtime: engine, profile_id: 'p', model: 'test-model' };
   const run = { id: 'run', status: 'running', seq: 2, message: '导出报告', output: body, binding, artifacts: [], pending: null, created_at: Date.now() / 1000,
    blocks: [{ type: 'tool', name: 'commandExecution', args: 'make report', result: 'done', done: true, resultCollapsed: true }, { type: 'text', content: body }] };
   let sessionReads = 0;
   await context.route('**/__artifact_order', route => route.fulfill({ contentType: 'text/html', body: html }));
   await context.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let value = [];
    if (url.pathname === '/api/runtime/sessions/session') {
     sessionReads++;
     value = { id: 'session', binding, runs: [run], artifacts: run.artifacts };
    } else if (url.pathname === '/api/models') value = { models: [], default: '' };
    else if (url.pathname === '/api/files') value = [{ name: report.name, path: report.path, is_dir: false, size: 32 }];
    else if (url.pathname === '/api/files/read') value = { content: '# 原位预览\n\n右侧文件区仍然可用。' };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
   });
   const page = await context.newPage();
   const errors = [];
   page.on('pageerror', error => { errors.push(error.message); console.error('PAGE', error.message); });
   page.on('console', message => { if (message.type() === 'error') console.error('CONSOLE', message.text()); });
   page.setDefaultTimeout(12000);
   await page.goto(base + '/__artifact_order');
   await page.waitForFunction(() => !!window.runtimeController).catch(async error => { console.error('PAGE_STATE', await page.locator('body').innerText()); await page.screenshot({path:join(tmpdir(),'jf-inline-order-error.png')}); throw error; });
   const row = page.locator('[data-runtime-query="run"]');
   const reportLink = row.locator(`[data-jf-file="${report.path}"]`);
   await reportLink.waitFor();
   const checkOrder = async (withFallback) => {
    assert.equal(await reportLink.count(), 1, 'archiving must not add a second file link before the answer');
    assert.equal(await reportLink.getAttribute('class'), 'jf-file-link', 'keep the original FILE renderer');
    const order = await row.evaluate((el, { report, notes, withFallback }) => {
     const first = [...el.querySelectorAll('p')].find(node => node.textContent === '说明在前');
     const last = [...el.querySelectorAll('p')].find(node => node.textContent === '总结在后');
     const link = el.querySelector(`[data-jf-file="${report}"]`);
     const fallback = el.querySelector(`[data-jf-file="${notes}"]`);
     const before = (a, b) => a && b && !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
     return before(first, link) && before(link, last) && (!withFallback || before(last, fallback));
    }, { report: report.path, notes: notes.path, withFallback });
    assert(order, 'keep prefix → original file → suffix → unmentioned file order');
    assert.equal(await row.getByRole('region', { name: '本轮文件' }).count(), 0);
   };
   await checkOrder(false);
   run.artifacts = [report, notes]; run.seq = 4;
   await page.evaluate(events => events.forEach(window.sendRuntimeEvent), [
    { seq: 3, type: 'artifact_created', payload: { artifact: report } },
    { seq: 4, type: 'artifact_created', payload: { artifact: notes } },
   ]);
   await row.locator(`[data-jf-file="${notes.path}"]`).waitFor();
   await checkOrder(true);
   run.status = 'completed'; run.seq = 5; run.finished_at = Date.now() / 1000;
   const refreshed = page.waitForResponse(response => response.url().includes('/api/runtime/sessions/session'));
   await page.evaluate(() => {
    window.sendRuntimeEvent({ seq: 5, type: 'completed', payload: {} });
    window.runtimeController.close();
   });
   await page.locator('[data-work-phase="settled"]').waitFor();
   await checkOrder(true);
   await refreshed;
   assert(sessionReads >= 2, 'terminal stream refreshes the persisted snapshot');
   await page.reload();
   await reportLink.waitFor();
   await checkOrder(true);
   await reportLink.click();
   await page.getByRole('tab', { name: /report.md/ }).waitFor();
   await page.locator('.jf-file-md-preview').getByText('右侧文件区仍然可用。').waitFor();
   assert.equal(await page.locator('vite-error-overlay').count(), 0);
   assert.deepEqual(errors, []);
   await page.screenshot({ path: join(tmpdir(), `jf-inline-artifact-order-${engine}.png`) });
   console.log(JSON.stringify({ engine, incremental_sse: true, archive_order: true, completed_order: true, history_order: true, original_file_style: true, no_duplicate: true, right_preview: true }));
   await context.close();
  }
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

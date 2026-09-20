const assert = require('node:assert/strict');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:3004';

(async () => {
 const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
 try {
  for (const engine of (process.env.TEST_ENGINES?.split(',') || ['codex', 'cursor', 'legacy'])) {
   const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
   await context.addInitScript(() => {
    localStorage.setItem('token', 'fixture-token'); if (!localStorage.getItem('jf-color')) localStorage.setItem('jf-color', 'dark');
    if (!localStorage.getItem('jf-style')) localStorage.setItem('jf-style', 'terminal'); localStorage.setItem('i18nextLng', 'zh');
   });
   // The UI test does not depend on the external font CDN.
   await context.route('https://fonts.googleapis.com/**', route => route.fulfill({ contentType: 'text/css', body: '' }));
   const binding = { runtime: engine, profile_id: 'profile', model: 'model-a' };
   const models = [{ id: 'model-a', name: 'Model A' }, { id: 'model-b', name: 'Model B' }];
   const profiles = engine === 'legacy' ? [] : [{ id: 'profile', name: '团队连接', runtime: engine, status: 'ready', models }];
   const file = '/docs/research.md', second = '/docs/summary.md';
   const output = `## 本周研究已整理\n\n完成了资料检索与文档更新，关键结论已汇总。\n\n<<FILE:${file}>>\n\n建议先确认三个待验证的问题，再安排下一轮工作。\n\n<<FILE:${second}>>`;
   const blocks = [{ type: 'tool', name: 'jellyfish_list_documents', args: '{"path":"/docs"}', result: 'research.md, summary.md', done: true }, { type: 'text', content: output }];
   const runs = [{ id: 'run', seq: 3, status: 'completed', message: '整理本周的研究资料，更新文档并总结下一步。', output, blocks, binding, artifacts: [], created_at: Date.now()/1000-14, finished_at: Date.now()/1000 }];
   const history = [{ role: 'user', content: runs[0].message }, { role: 'assistant', content: output, blocks }];
   const conversations = ['本周研究与文档整理', '产品方案讨论', '脚本与数据分析'].map((title, i) => ({ id: 'conv-'+i, title, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), messages: history,
    ...(engine === 'legacy' ? {} : { runtime_binding: binding, runtime_session_id: 'session' }) }));
   const sent = [], errors = [], deletes = [];
   await context.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname; let value = [];
    if (path === '/api/auth/me') value = { user_id: 'fixture', username: 'shinan' };
    else if (path === '/api/models') value = { models, default: 'model-a' };
    else if (path.startsWith('/api/settings/api-keys')) value = { has_llm: true, openai_api_key_configured: true, platform_configured: { openai: true } };
    else if (path === '/api/runtime/capabilities') value = { available: true, enabled: true };
    else if (path === '/api/runtime/profiles') value = profiles;
    else if (path === '/api/runtime/preferences') value = engine === 'legacy' ? { runtime: 'deepagents', model: 'model-a' } : binding;
    else if (path.includes('streaming-status')) value = { streaming: [], interrupted: [] };
    else if (path.includes('interrupt')) value = { has_interrupt: false };
    else if (path.endsWith('/preferences')) value = { language: 'zh', tz_offset_hours: 8 };
    else if (path === '/api/conversations') value = conversations;
    else if (path.startsWith('/api/conversations/')) {
     if (route.request().method() === 'DELETE') deletes.push(path);
     value = conversations.find(c => path.endsWith(c.id)) || conversations[0];
    } else if (path === '/api/runtime/sessions/session') value = { id: 'session', binding, runs, artifacts: [] };
    else if (path === '/api/files') value = [file, second].map(path => ({ name: path.split('/').pop(), path, is_dir: false, size: 240 }));
    else if (path === '/api/files/read') value = { content: '# 研究记录\n\n## 已完成\n\n- 整理资料与已有结论\n- 补充验证方向\n\n## 下一步\n\n确认优先级，再进入下一轮验证。' };
    else if (path === '/api/runtime/turns' || path === '/api/chat') {
     const body = route.request().postDataJSON(); sent.push(body);
     if (engine === 'legacy') { await route.fulfill({ contentType: 'text/event-stream', body: 'data: {"type":"token","content":"收到"}\n\n' }); return; }
     value = { id: 'new-run', seq: 1, message: body.message, output: '收到', blocks: [{ type: 'text', content: '收到' }], status: 'completed', binding: { ...binding, model: body.model }, artifacts: [], created_at: Date.now()/1000, finished_at: Date.now()/1000 };
     runs.push(value);
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
   });
   const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', e => errors.push(e.message));
   await page.goto(base, { waitUntil: 'domcontentloaded' });
   const list = page.getByRole('navigation', { name: '对话记录' });
   const search = page.getByRole('textbox', { name: '搜索对话' });
   await search.fill('研究');
   assert.equal(await list.locator('[class*="convSelect"]').count(), 1);
   await search.fill('不存在的对话'); await list.getByText('没有找到匹配的对话').waitFor();
   await search.press('Escape'); assert.equal(await list.locator('[class*="convSelect"]').count(), 3);
   await list.getByRole('button', { name: conversations[0].title, exact: true }).focus();
   await page.keyboard.press('Enter');
   await page.locator(`[data-jf-file="${file}"]`).first().waitFor();
   const composer = page.locator('[data-chat-composer]');
   const editor = composer.getByRole('textbox');
   await editor.waitFor();
   assert.equal(await composer.getByRole('button', { name: '添加图片或文件', exact: true }).count(), 1);
   assert.equal(await composer.locator('[class*="voiceBtn"]').count(), 1);
   assert(await composer.getByRole('button', { name: '发送消息', exact: true }).isDisabled());
   if (engine === 'codex') {
    assert(await page.evaluate(async () => {
      const nativeReader = window.FileReader;
      const { loadPptxViewer } = await import('/src/components/office/pptxViewer.ts');
      const [first, second] = await Promise.all([loadPptxViewer(), loadPptxViewer()]);
      return window.FileReader === nativeReader && first === second
        && typeof first.PPTXViewer === 'function' && typeof new FileReader().readAsDataURL === 'function';
    }), 'loading the PPT viewer preserves the native upload reader');
   }
   const chooser = page.waitForEvent('filechooser');
   await composer.getByRole('button', { name: '添加图片或文件', exact: true }).click();
   await (await chooser).setFiles({ name: 'sample.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lq8AAAAASUVORK5CYII=', 'base64') });
   await composer.getByRole('img', { name: 'sample.png' }).waitFor();
   const remove = composer.getByRole('button', { name: '移除附件：sample.png', exact: true });
   await remove.focus(); await remove.press('Enter');
   assert.equal(await composer.getByRole('img', { name: 'sample.png' }).count(), 0);
   await editor.fill('第一行'); await editor.press('Shift+Enter'); await editor.press('a');
   assert((await editor.innerText()).includes('\n'), 'Shift+Enter inserts a line, not a turn');
   await editor.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true });
   assert.equal(sent.length, 0, 'IME confirmation must not submit');
   await editor.fill('确认模型切换与发送');
   await composer.locator('.ant-select-selector').click();
   await page.locator('.ant-select-item-option').filter({ hasText: 'Model B' }).click();
   const submitted = page.waitForResponse(r => /\/api\/(chat|runtime\/turns)$/.test(new URL(r.url()).pathname));
   await editor.press('Enter');
   await submitted;
   assert.equal(sent.length, 1); assert.equal(sent[0].model, 'model-b'); assert.equal(sent[0].message, '确认模型切换与发送');
   await list.getByRole('button', { name: conversations[0].title, exact: true }).click();
   await page.locator(`[data-jf-file="${file}"]`).first().click();
   await page.getByRole('tab', { name: /research.md/ }).waitFor();
   const separator = page.getByRole('separator', { name: '调整聊天与文件宽度' });
   await separator.focus(); await separator.press('Enter');
   assert.equal(await separator.getAttribute('aria-valuenow'), '50');
   await separator.press('ArrowRight'); assert.equal(await separator.getAttribute('aria-valuenow'), '53');
   await separator.press('Enter');
   await page.locator(`[data-jf-file="${second}"]`).first().click();
   const tab = page.getByRole('tab', { name: /summary.md/ }); await tab.focus(); await tab.press('ArrowLeft');
   assert.equal(await page.getByRole('tab', { name: /research.md/ }).getAttribute('aria-selected'), 'true');
   await page.getByRole('button', { name: '更多文件操作', exact: true }).click();
   await page.getByRole('menuitem', { name: '关闭当前标签', exact: true }).waitFor(); await page.keyboard.press('Escape');
   assert(await page.getByRole('button', { name: '下载当前文件', exact: true }).isVisible());
   await page.screenshot({ animations: 'disabled', path: join(tmpdir(), `jf-workspace-${engine}.png`) });
   await page.getByRole('button', { name: '对话全屏', exact: true }).click();
   await page.getByRole('button', { name: '关闭文件面板', exact: true }).click();
   await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
   assert.equal(await search.isVisible(), false);
   await page.getByRole('button', { name: '展开侧栏', exact: true }).press('Enter');
   assert(await search.isVisible());
   await page.screenshot({ animations: 'disabled', path: join(tmpdir(), `jf-workspace-${engine}-chat.png`) });
   await page.setViewportSize({ width: 390, height: 844 });
   await page.getByRole('button', { name: '打开菜单', exact: true }).click();
   await page.getByRole('navigation', { name: '对话记录' }).getByRole('button', { name: conversations[1].title, exact: true }).click();
   await page.getByRole('button', { name: '打开菜单', exact: true }).waitFor();
   await page.locator('.ant-drawer-content-wrapper').waitFor({ state: 'hidden' });
   assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
   const bounds = await composer.boundingBox(); assert(bounds.x >= 0 && bounds.x + bounds.width <= 391);
   assert(await composer.getByRole('button', { name: '发送消息', exact: true }).isVisible());
   await page.screenshot({ animations: 'disabled', path: join(tmpdir(), `jf-workspace-${engine}-mobile.png`) });
   await page.setViewportSize({ width: 1440, height: 1000 });
   await page.getByRole('button', { name: '切换浅色', exact: true }).click();
   assert.equal(await page.locator('html').getAttribute('data-color'), 'light');
   await page.screenshot({ animations: 'disabled', path: join(tmpdir(), `jf-workspace-${engine}-light.png`) });
   if (engine === 'codex') {
    await page.evaluate(() => localStorage.setItem('jf-style', 'regular')); await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('navigation', { name: '对话记录' }).getByRole('button', { name: conversations[0].title, exact: true }).click();
    await page.locator(`[data-jf-file="${file}"]`).first().waitFor();
    assert.equal(await page.locator('html').getAttribute('data-style'), 'regular');
    await page.screenshot({ animations: 'disabled', path: join(tmpdir(), 'jf-workspace-regular-light.png') });
   }
   assert.equal(deletes.length, 0, 'selecting conversations never deletes them');
   assert.deepEqual(errors, []); assert.equal(await page.locator('vite-error-overlay').count(), 0);
   console.log(JSON.stringify({ engine, shared_composer: true, attachment_upload_remove: true, search_keyboard: true, ime_safe: true, model_and_send: true, file_tab_keyboard: true, resize_keyboard: true, mobile_navigation: true, light_theme: true, page_errors: 0 }));
   await context.close();
  }
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

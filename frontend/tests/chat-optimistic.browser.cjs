const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:3004/';

function gate() {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
}

async function waitFor(check) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error('Timed out waiting for intercepted request');
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chromium' });
  try {
    const context = await browser.newContext({ viewport: { width: 1001, height: 1024 } });
    await context.addInitScript(() => {
      localStorage.setItem('token', 'fixture-token');
      localStorage.setItem('jf-color', 'dark');
      localStorage.setItem('i18nextLng', 'zh');
    });
    const conversations = [];
    const messages = new Map();
    const createGates = [];
    const chatGates = [];
    let createCount = 0;
    let chatCount = 0;
    let failNextCreate = false;
    let failNextChat = false;
    const errors = [];

    await context.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      const method = route.request().method();
      let value = [];
      if (path === '/api/auth/me') value = { user_id: 'fixture', username: 'fixture' };
      else if (path === '/api/models') value = { models: [{ id: 'fixture-model', name: 'Fixture Model' }], default: 'fixture-model' };
      else if (path.startsWith('/api/settings/api-keys')) value = { has_llm: true, openai_api_key_configured: true, platform_configured: { openai: true } };
      else if (path === '/api/runtime/capabilities') value = { available: true };
      else if (path === '/api/runtime/profiles') value = [];
      else if (path === '/api/runtime/preferences') value = { runtime: 'deepagents' };
      else if (path === '/api/chat/streaming-status') value = { streaming: [], interrupted: [] };
      else if (path === '/api/conversations' && method === 'GET') value = conversations;
      else if (path === '/api/conversations' && method === 'POST') {
        createCount++;
        const pending = gate();
        createGates.push(pending);
        await pending.promise;
        if (failNextCreate) {
          failNextCreate = false;
          await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '创建失败' }) });
          return;
        }
        const conv = {
          id: `conv-${createCount}`, title: route.request().postDataJSON().title,
          created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
          runtime_binding: { runtime: 'deepagents' }, runtime_session_id: null,
        };
        conversations.unshift(conv);
        messages.set(conv.id, []);
        value = conv;
      } else if (path.startsWith('/api/conversations/') && method === 'GET') {
        const id = path.split('/').pop();
        value = { ...conversations.find(conv => conv.id === id), messages: messages.get(id) || [] };
      } else if (path === '/api/chat' && method === 'POST') {
        chatCount++;
        const pending = gate();
        chatGates.push(pending);
        const body = route.request().postDataJSON();
        const shouldFail = failNextChat;
        failNextChat = false;
        await pending.promise;
        if (shouldFail) {
          await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '发送失败' }) });
          return;
        }
        messages.get(body.conversation_id).push(
          { role: 'user', content: body.message },
          { role: 'assistant', content: `回复 ${chatCount}` },
        );
        await route.fulfill({
          contentType: 'text/event-stream',
          body: `data: ${JSON.stringify({ type: 'token', content: `回复 ${chatCount}` })}\n\ndata: {"type":"done"}\n\n`,
        });
        return;
      }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    });

    const page = await context.newPage();
    page.setDefaultTimeout(12000);
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    const composer = page.locator('[data-chat-composer]');
    const send = composer.getByRole('button', { name: '发送消息', exact: true });
    const userTurns = page.locator('[data-jf-msg-role="user"]');
    await composer.getByRole('textbox').fill('第一条慢请求');
    await send.click();
    await page.waitForFunction(() => document.querySelectorAll('[data-jf-msg-role="user"]').length === 1);
    assert.equal(await userTurns.count(), 1, 'first user turn paints before createConversation settles');
    await page.getByText('正在准备会话', { exact: true }).waitFor();
    assert.equal(createCount, 1);
    assert.equal(chatCount, 0);
    assert.equal(await send.isDisabled(), true);

    createGates[0].release();
    await page.waitForFunction(() => document.querySelector('[data-jf-msg-role="user"]')?.textContent?.includes('第一条慢请求'));
    await page.waitForFunction(() => document.querySelector('[data-work-phase="working"]')?.textContent?.includes('正在准备回答'));
    assert.equal(chatCount, 1);
    assert.equal(await userTurns.count(), 1, 'creation must not append a duplicate user turn');
    chatGates[0].release();
    await page.getByText('回复 1', { exact: true }).waitFor();
    assert.equal(await userTurns.count(), 1, 'history reconciliation must not duplicate the optimistic turn');

    await composer.getByRole('textbox').fill('已有对话慢请求');
    await send.evaluate(element => { element.click(); element.click(); });
    await page.getByText('已有对话慢请求', { exact: true }).waitFor();
    assert.equal(await userTurns.count(), 2, 'existing conversation also paints before /chat settles');
    assert.equal(chatCount, 2, 'same-render double click must not submit twice');
    chatGates[1].release();
    await page.getByText('回复 2', { exact: true }).waitFor();
    assert.equal(await userTurns.count(), 2);

    failNextChat = true;
    await composer.getByRole('textbox').fill('发送失败后保留');
    await send.click();
    await page.getByText('发送失败后保留', { exact: true }).waitFor();
    assert.equal(chatCount, 3);
    chatGates[2].release();
    await page.getByText('上一条消息未送达，内容已保留').waitFor();
    assert.equal(await userTurns.count(), 2, 'rejected /chat request must remove only the unconfirmed turn');
    await page.getByRole('button', { name: '恢复到输入框' }).click();
    assert.equal(await composer.getByRole('textbox').textContent(), '发送失败后保留');

    await page.getByRole('button', { name: '新对话' }).click();
    failNextCreate = true;
    await composer.getByRole('textbox').fill('失败后保留草稿');
    await send.click();
    await page.getByText('失败后保留草稿', { exact: true }).waitFor();
    assert.equal(createCount, 2);
    createGates[1].release();
    await page.waitForFunction(() => document.querySelector('[data-chat-composer] [role="textbox"]')?.textContent?.includes('失败后保留草稿'));
    assert.equal(await userTurns.count(), 0, 'failed create rolls back the provisional turn');
    assert.equal(await composer.getByRole('textbox').textContent(), '失败后保留草稿');

    await page.getByRole('button', { name: '新对话' }).click();
    await composer.getByRole('textbox').fill('迟到创建响应');
    await send.click();
    await page.getByText('迟到创建响应', { exact: true }).waitFor();
    await page.locator('[class*="convSelect"]').filter({ hasText: '第一条慢请求' }).click();
    await page.waitForFunction(() => document.querySelector('[class*="chatTitle"]')?.textContent === '第一条慢请求');
    createGates[2].release();
    await page.waitForFunction(() => document.querySelector('[class*="streamElsewhereBanner"]'));
    assert.equal(await page.locator('[class*="chatTitle"]').textContent(), '第一条慢请求', 'late create must not switch the viewed conversation');
    assert.equal(await userTurns.filter({ hasText: '迟到创建响应' }).count(), 0, 'late submission must not leak into another conversation');
    await waitFor(() => chatGates.length === 4);
    chatGates[3].release();
    await page.locator('[class*="streamElsewhereBanner"]').waitFor({ state: 'hidden' });
    assert.deepEqual(errors, []);
    await context.close();
    console.log(JSON.stringify({ optimistic_create: true, optimistic_existing: true, no_duplicate: true, failed_create_restores_draft: true, rejected_chat_preserves_message: true, stale_create_does_not_switch: true, page_errors: 0 }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

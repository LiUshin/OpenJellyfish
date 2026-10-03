/** Real browser regression for delayed scheduler detail requests. */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:3001/';

function gate() {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
}

const tasks = [
  { id: 'task-a', name: '任务 A', description: 'A 的内容', task_type: 'agent', schedule_type: 'once', enabled: true, task_config: { prompt: 'A prompt' } },
  { id: 'task-b', name: '任务 B', description: 'B 的内容', task_type: 'agent', schedule_type: 'once', enabled: true, task_config: { prompt: 'B prompt' } },
];

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
  const slowA = gate();
  const slowB = gate();
  const aRequested = gate();
  const bRequested = gate();
  try {
    const context = await browser.newContext({ viewport: { width: 1001, height: 1024 } });
    await context.addInitScript(() => {
      localStorage.setItem('token', 'fixture-token');
      localStorage.setItem('jf-color', 'dark');
      localStorage.setItem('i18nextLng', 'zh');
    });
    const errors = [];
    await context.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      let value = [];
      if (path === '/api/auth/me') value = { user_id: 'fixture', username: 'fixture' };
      else if (path === '/api/settings/api-keys/status') value = { has_llm: true, has_openai: true, has_anthropic: false };
      else if (path === '/api/scheduler') value = tasks;
      else if (path === '/api/scheduler/services/all') value = [];
      else if (path === '/api/scheduler/task-a') {
        aRequested.release();
        await slowA.promise;
        value = tasks[0];
      } else if (path === '/api/scheduler/task-b') {
        bRequested.release();
        await slowB.promise;
        value = tasks[1];
      }
      else if (path === '/api/scheduler/task-a/runs' || path === '/api/scheduler/task-b/runs') value = [];
      else if (path === '/api/runtime/profiles') value = [];
      else if (path === '/api/models') value = { models: [], default: '' };
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    });

    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(new URL('settings/scheduler', base).href);
    const row = name => page.getByText(name, { exact: true }).first().locator('xpath=../..');
    const selected = async name => assert.match(await row(name).getAttribute('style'), /background:\s*var\(--jf-menu-selected-bg\)/);
    await row('任务 A').waitFor();
    await row('任务 B').waitFor();

    await row('任务 A').click();
    await aRequested.promise;
    await selected('任务 A');
    await page.locator('.settings-detail-welcome[role="status"]').getByText('正在加载任务…').waitFor();
    assert.equal(await page.locator('.settings-detail-welcome[role="status"]').getByText('任务 A').count(), 1,
      'the first selection should show its loading state before the API responds');

    await row('任务 B').click();
    await bRequested.promise;
    await selected('任务 B');
    assert.doesNotMatch(await row('任务 A').getAttribute('style'), /background:\s*var\(--jf-menu-selected-bg\)/);
    await page.locator('.settings-detail-welcome[role="status"]').getByText('任务 B').waitFor();
    slowB.release();
    await page.getByRole('heading', { name: '任务 B', exact: true }).waitFor();

    const lateA = page.waitForResponse(response => new URL(response.url()).pathname === '/api/scheduler/task-a');
    slowA.release();
    await lateA;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await selected('任务 B');
    assert.doesNotMatch(await row('任务 A').getAttribute('style'), /background:\s*var\(--jf-menu-selected-bg\)/);
    assert.equal(await page.getByRole('heading', { name: '任务 B', exact: true }).count(), 1,
      'late A response must not replace B details');
    assert.equal(await page.getByRole('heading', { name: '任务 A', exact: true }).count(), 0);
    assert.deepEqual(errors, []);
    await context.close();
    console.log(JSON.stringify({ immediate_loading: true, late_response_keeps_task_b: true, page_errors: 0 }));
  } finally {
    slowA.release();
    slowB.release();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

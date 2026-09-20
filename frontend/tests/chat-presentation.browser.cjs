const { join } = require('node:path');
const { tmpdir } = require('node:os');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:3004';
const html = `<!doctype html><html data-color="dark" data-style="terminal"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>(type)=>type; window.__vite_plugin_react_preamble_installed__=true;
const React=(await import('/node_modules/.vite/deps/react.js')).default;
const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
await import('/src/styles/global.css'); await import('/src/i18n/index.ts');
const {default:StreamingMessage}=await import('/src/pages/Chat/components/StreamingMessage.tsx');
const {default:QueryNavigation}=await import('/src/pages/Chat/components/QueryNavigation.tsx');
const {default:ServiceToolBadge}=await import('/src/service-chat/ServiceToolBadge.tsx');
const root=createRoot(document.getElementById('root')); const h=React.createElement;
window.fixture={status:'running',blocks:[],items:Array.from({length:1000},(_,i)=>({id:String(i),question:'第 '+(i+1)+' 轮：分析文档并整理更新方案',answer:'已整理成三个阶段：检查现有实现、改进交互、验证效果。'})),activeId:'3',service:false};
window.setFixture=update=>{Object.assign(window.fixture,update);const f=window.fixture;root.render(h('main',{style:{position:'relative',height:'90vh',maxWidth:960,margin:'24px auto',padding:'24px 24px 24px 56px',overflow:'auto'}},
 h(QueryNavigation,{items:f.items,activeId:f.activeId,onJump:id=>window.lastJump=id}),
 h('h2',null,'Jellyfish · 对话展示验收'),
 h(StreamingMessage,{blocks:f.blocks,isStreaming:!['completed','cancelled','failed'].includes(f.status),status:f.status,startedAt:Date.now()/1000-30,toolRenderer:f.service?ServiceToolBadge:undefined,hideSubagents:f.service,scheduledTaskFriendlyMode:f.service})
));return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));};window.setFixture({});
</script></body></html>`;
const tool=(name,done=true,result='已完成')=>({type:'tool',name,args:'{"path":"/docs/README.md"}',result:done?result:'',done,resultCollapsed:true});
const text=content=>({type:'text',content});
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || 'chromium'});
 try {
  const page=await browser.newPage({viewport:{width:1200,height:900}});page.setDefaultTimeout(10000);const errors=[];
  page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE',e.message)});page.on('console',m=>{if(m.type()==='error')console.error('CONSOLE',m.text())});
  await page.route('**/__ui_review',r=>r.fulfill({contentType:'text/html',body:html}));
  await page.goto(base+'/__ui_review'); await page.waitForFunction(()=>!!window.setFixture);
  await page.getByRole('heading',{name:'Jellyfish · 对话展示验收'}).waitFor();
  const header=page.locator('[data-work-phase] > button');
  assert.equal(await header.getAttribute('aria-expanded'),'true');
  assert.equal(await page.locator('[class*="pixels"] i').count(),25);
  await page.evaluate(blocks=>window.setFixture({blocks}),[text('先检查长期记忆和文档目录。'),tool('jellyfish_read_memory'),tool('jellyfish_list_documents',false)]);
  await header.click();assert.equal(await header.getAttribute('aria-expanded'),'false');
  await page.evaluate(blocks=>window.setFixture({blocks}),[text('先检查长期记忆和文档目录。'),tool('jellyfish_read_memory'),tool('jellyfish_list_documents'),tool('web_search',false)]);
  assert.equal(await header.getAttribute('aria-expanded'),'false','new tool must not reopen work');
  await header.click();
  const blocks=[];for(let i=0;i<28;i++)blocks.push(text('进展 '+i+'：核对接口行为，保留当前功能并验证消息的时间顺序。'),tool('read_file'));
  blocks.push(tool('web_search',false));
  await page.evaluate(blocks=>window.setFixture({blocks}),blocks); await settle(page);
  const progress=page.getByLabel('本轮执行进展',{exact:true});
  assert(await progress.evaluate(el=>el.scrollHeight>el.clientHeight+100));
  assert(await progress.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight<25),'follow latest');
  await progress.evaluate(el=>{window.progressBox=el;el.scrollTop=0;el.dispatchEvent(new Event('scroll',{bubbles:true}));});
  blocks.push(text('新的进展：正在查阅外部资料。'));
  await page.evaluate(blocks=>window.setFixture({blocks}),blocks);await settle(page);
  assert(await progress.evaluate(el=>el===window.progressBox && el.scrollTop===0),'stable DOM and preserve reading position');
  await page.getByRole('button',{name:'回到最新进展',exact:true}).click();await settle(page);
  assert(await progress.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight<25));
  await page.evaluate(()=>window.setFixture({status:'waiting_approval'}));
  assert.equal(await header.getAttribute('aria-expanded'),'false');
  await page.evaluate(()=>window.setFixture({status:'running'}));
  assert.equal(await header.getAttribute('aria-expanded'),'true');
  await page.evaluate(blocks=>window.setFixture({status:'completed',blocks}),[text('已查看文档。'),tool('read_file'),tool('web_search',true,'失败'),text('## 建议方案\n\n保留荧光效果，升级左侧导航与工具进展。')]);
  assert.equal(await header.getAttribute('aria-expanded'),'false');
  await page.getByRole('heading',{name:/^建议方案/}).waitFor();
  await header.click();await page.getByRole('button',{name:/工具调用/}).click();
  assert(await page.locator('[data-outcome="failed"]').isVisible());
  await page.getByRole('button',{name:/读取文件.*已完成/}).click();
  await page.getByText('{"path":"/docs/README.md"}',{exact:true}).waitFor();
  // Wave, glow, preview, keyboard access to virtualized rows.
  const nav=page.getByRole('navigation',{name:'对话问答导航'});
  assert(await nav.locator('button').count()<30);
  await nav.locator('[data-query-position="3"]').hover();await page.getByRole('tooltip').waitFor();
  const widths=await nav.locator('[data-query-position]').evaluateAll(nodes=>Object.fromEntries(nodes.map(n=>[n.dataset.queryPosition,parseFloat(n.firstChild.style.width)])));
  assert.equal(widths[3],30);assert.equal(widths[2],24);assert.equal(widths[4],24);assert.equal(widths[1],18);
  assert(await nav.locator('[data-query-position="3"] span').evaluate(el=>getComputedStyle(el).boxShadow!=='none'));
  const navBox=await nav.boundingBox();assert(navBox.x<200,'navigation stays on left');
  await nav.locator('[data-query-position="3"]').click();assert.equal(await page.evaluate(()=>window.lastJump),'3');
  await nav.locator('[data-query-position="3"]').press('End');await settle(page);
  assert.equal(await page.evaluate(()=>document.activeElement?.dataset.queryPosition),'999');
  await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>window.lastJump),'999');
  await page.keyboard.press('Home');await settle(page);
  assert.equal(await page.evaluate(()=>document.activeElement?.dataset.queryPosition),'0');
  await page.keyboard.press('Escape');assert.equal(await page.getByRole('tooltip').count(),0);
  // Service labels remain filtered even when work/group is expanded.
  await page.evaluate(blocks=>window.setFixture({status:'running',service:true,blocks}),[{type:'thinking',content:'PRIVATE_REASONING',collapsed:false},tool('contact_admin',false),{type:'subagent',name:'PRIVATE_SUBAGENT',task:'secret',status:'running',content:'SECRET',tools:[],timeline:[],collapsed:false,done:false}]);
  assert.equal(await page.getByText('contact_admin',{exact:true}).count(),0);
  assert(!/PRIVATE_REASONING|PRIVATE_SUBAGENT|SECRET/.test(await page.locator('body').innerText()));
  assert.equal(await page.getByText('{"path":"/docs/README.md"}',{exact:true}).count(),0);
  await page.evaluate(blocks=>window.setFixture({service:false,status:'running',blocks,items:window.fixture.items.slice(0,12)}),[text('先查看长期记忆，再整理文档。'),tool('jellyfish_read_memory'),tool('jellyfish_list_documents'),text('目录已读取，正在查找与当前问题有关的资料。'),tool('web_search',false)]);
  await nav.locator('[data-query-position="3"]').hover();
  await page.screenshot({path:join(tmpdir(),'jf-ui-progress-dark.png')});
  await page.evaluate(()=>document.documentElement.dataset.color='light');
  await page.screenshot({path:join(tmpdir(),'jf-ui-progress-light.png')});
  await page.setViewportSize({width:390,height:844});await settle(page);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no mobile horizontal overflow');
  await page.screenshot({path:join(tmpdir(),'jf-ui-progress-mobile.png')});
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('[class*="pixels"] i').first().evaluate(el=>getComputedStyle(el).animationName),'none');
  assert.deepEqual(errors,[]);console.log('PASS: progress phases, manual collapse, scroll pinning, final answer, failure state, left wave/glow, QA preview, 1000-row keyboard navigation, Service privacy, mobile, reduced motion; no page errors.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});

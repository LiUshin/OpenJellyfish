const { join } = require('node:path');
const { tmpdir } = require('node:os');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const tool=(name,done=true)=>({type:'tool',name,args:'{"path":"/docs/README.md"}',result:done?'已完成':'',done,resultCollapsed:true});
const text=content=>({type:'text',content});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || 'chromium'});
 try {
  for(const engine of ['codex','cursor','legacy']) {
   const context=await browser.newContext({viewport:{width:1440,height:1000}});
   await context.addInitScript(()=>{localStorage.setItem('token','fixture-token');localStorage.setItem('jf-color','dark');localStorage.setItem('jf-style','terminal');localStorage.setItem('i18nextLng','zh');});
   const binding={runtime:engine,profile_id:'fixture-'+engine,model:'fixture-model',image_mode:'native'};
   const profiles=engine==='legacy'?[]:[{id:binding.profile_id,name:'本人连接',runtime:engine,status:'ready',models:[{id:'fixture-model',name:'Fixture Model'}],source:'owner_shared',recovery_required:false}];
   const now=Date.now()/1000;
   const runs=Array.from({length:8},(_,i)=>({id:'run-'+i,seq:3,status:'completed',message:['检查一下当前文档','先整理已有记忆','把项目结构列一下','确认这次改版的范围','哪些交互需要保留','给我一个实现顺序','增加流式状态展示','说明一下验收方式'][i],output:'保留 Jellyfish 的荧光效果，逐步验证执行过程、导航预览和移动端显示。',blocks:[text('先检查工作区中的相关内容。'),tool('jellyfish_read_memory'),tool('jellyfish_list_documents'),text('保留 Jellyfish 的荧光效果，逐步验证执行过程、导航预览和移动端显示。')],binding,artifacts:[],pending:null,created_at:now-100*(9-i),finished_at:now-100*(9-i)+12}));
   const active={id:'run-8',seq:1,status:'waiting_approval',message:'按这个方向改版，并保留我们的荧光效果。',output:'已经读取目录，接下来检查现有组件。',blocks:[text('已经读取目录，接下来检查现有组件。'),tool('jellyfish_read_memory'),tool('jellyfish_list_documents'),tool('commandExecution',false)],binding,artifacts:[],created_at:now-32,pending:{id:'approval-1',kind:'command',command:'echo review',allowed:['accept','decline']}};
   runs.push(active);
   const history=runs.slice(0,8).flatMap(run=>[{role:'user',content:run.message},{role:'assistant',content:run.output,blocks:run.blocks}]);
   const conv={id:'ui-review',title:'交互改版验收',created_at:new Date().toISOString(),updated_at:new Date().toISOString(),messages:history,...(engine==='legacy'?{}:{runtime_binding:binding,runtime_session_id:'fixture-session'})};
   const errors=[];
   await context.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;let value=[];
    if(path==='/api/auth/me')value={user_id:'fixture',username:'fixture'};
    else if(path==='/api/models')value={models:[{id:'legacy',name:'自挂模型'}],default:'legacy'};
    else if(path.startsWith('/api/settings/api-keys'))value={has_llm:true,openai_api_key_configured:true,platform_configured:{openai:true}};
    else if(path==='/api/runtime/capabilities')value={enabled:true,available:true};
    else if(path==='/api/runtime/profiles')value=profiles;
    else if(path==='/api/runtime/preferences')value=engine==='legacy'?{runtime:'deepagents',model:'legacy'}:binding;
    else if(path.includes('streaming'))value={streaming:[],interrupted:[]};
    else if(path.includes('interrupt'))value={has_interrupt:false};
    else if(path.endsWith('/preferences'))value={tz_offset_hours:8};
    else if(path==='/api/conversations')value=[conv];
    else if(path==='/api/conversations/ui-review')value=conv;
    else if(path==='/api/runtime/sessions/fixture-session')value={id:'fixture-session',binding,runs,artifacts:[]};
    else if(path.endsWith('/events')) {await new Promise(resolve=>context.once('close',resolve));return;}
    else if(path.endsWith('/cancel')) {active.status='cancelled';active.finished_at=Date.now()/1000;active.pending=null;value={status:'cancelled'};}
    else if(path.endsWith('/approve')) {assert.equal(route.request().postDataJSON().decision,'accept');active.status='running';active.pending=null;active.blocks=[...active.blocks.slice(0,-1),text('组件已定位，正在检索可复用的交互模式。'),tool('web_search',false)];value={status:'accepted'};}
    await route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
   });
   const page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));
   await page.goto(process.env.TEST_BASE_URL || 'http://127.0.0.1:3004/');await page.getByText('交互改版验收',{exact:true}).first().click();
   const nav=page.getByRole('navigation',{name:'对话问答导航'});await nav.waitFor();
   if(engine!=='legacy') {
    await page.getByRole('button',{name:'允许本次',exact:true}).waitFor();
    assert.equal(await page.locator('[data-work-phase="waiting"] > button').getAttribute('aria-expanded'),'false');
    await page.getByRole('button',{name:'允许本次',exact:true}).click();
    await page.locator('[data-work-phase="working"]').waitFor();
    assert.equal(await page.locator('[data-work-phase="working"] > button').getAttribute('aria-expanded'),'true');
    assert.equal(await page.getByRole('button',{name:'允许本次',exact:true}).count(),0);
   }
   await nav.locator('[data-query-position="6"]').hover();await page.getByRole('tooltip').waitFor();
   const rail=await nav.boundingBox(),chat=await page.locator('[class*="chatArea"]').boundingBox();
   assert(rail.x-chat.x<10,'left rail in actual chat area');
   assert((await page.getByRole('tooltip').innerText()).includes('增加流式状态展示'));
   await page.screenshot({path:join(tmpdir(),'jf-ui-app-'+engine+'.png')});
   await nav.locator('[data-query-position="0"]').click();
   await page.waitForFunction(()=>document.querySelector('nav[aria-label="对话问答导航"] [data-query-position="0"]')?.getAttribute('aria-current')==='true');
   if(engine!=='legacy') {
    const composer=page.locator('[data-chat-composer]');
    await composer.getByRole('textbox').fill('下一条消息草稿');
    assert(await composer.getByRole('button',{name:'发送消息',exact:true}).isDisabled());
    await composer.getByRole('button',{name:'停止生成',exact:true}).click();
    await composer.getByRole('button',{name:'停止生成',exact:true}).waitFor({state:'hidden'});
    assert((await composer.getByRole('textbox').innerText()).includes('下一条消息草稿'));
    assert(await composer.getByRole('button',{name:'发送消息',exact:true}).isEnabled());
   }
   assert.equal(await page.locator('vite-error-overlay').count(),0);assert.deepEqual(errors,[]);
   console.log(JSON.stringify({engine,actual_app:true,mocked_api:true,left_navigation:true,approval_controls_preserved:engine!=='legacy',stop_preserves_draft:engine!=='legacy',page_errors:0}));
   await context.close();
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

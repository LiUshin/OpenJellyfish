/** Actual browser + isolated real API fixture; never use a production host. */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:3120';
assert(['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname),'Use an isolated loopback fixture only');
(async () => {
 const fixture = await (await fetch(base+'/__fixture')).json();
 assert.equal(fixture.service.name,'Service 消息验收');
 const browser = await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || 'chromium'});
 const errors=[];
 try {
  const admin=await browser.newContext({viewport:{width:1440,height:1000}});
  await admin.addInitScript(()=>{
   localStorage.setItem('token','fixture-only');localStorage.setItem('i18nextLng','zh');
   // randomUUID is unavailable on ordinary HTTP self-hosting. Reply and notice
   // submission must still work with getRandomValues.
   Object.defineProperty(Crypto.prototype,'randomUUID',{value:undefined,configurable:true});
  });
  const a=await admin.newPage();a.on('pageerror',e=>errors.push(e.message));a.setDefaultTimeout(15000);
  await a.goto(base+'/settings/inbox');
  await a.getByRole('button',{name:'查看与回复',exact:true}).first().waitFor();
  const dismiss=a.getByRole('button',{name:'稍后设置',exact:true});
  if(await dismiss.isVisible()){await dismiss.click();await dismiss.waitFor({state:'hidden'});}
  // Opening an unread case removes it from this list, but its independent
  // details must keep updating as the reply moves from queued to delivered.
  await a.getByRole('button',{name:/^未\s*读$/}).click();
  const consumer=await browser.newContext({viewport:{width:1280,height:900}});
  await consumer.addInitScript(({sid,key,convs})=>{
   localStorage.setItem('i18nextLng','zh');localStorage.setItem('svc_key_'+sid,key);
   localStorage.setItem('svc_convs_'+sid,JSON.stringify({activeId:convs[0].id,items:convs.map(c=>({id:c.id,title:c.title,updatedAt:c.updated_at}))}));
  },{sid:fixture.service.id,key:fixture.key,convs:fixture.conversations.slice(0,2)});
  const c=await consumer.newPage();c.on('pageerror',e=>errors.push(e.message));c.setDefaultTimeout(15000);
  await c.goto(base+'/service-chat.html');
  await c.getByText('请管理员确认我的售后问题。',{exact:true}).waitFor();
  await a.getByRole('button',{name:'查看与回复',exact:true}).first().click();
  await a.getByRole('button',{name:'查看原会话',exact:true}).click();
  await a.getByText('请管理员确认我的售后问题。',{exact:true}).waitFor();
  const reply='管理员测试回复 '+Date.now();
  await a.getByRole('textbox',{name:'回复内容',exact:true}).fill(reply);
  await a.getByRole('button',{name:'提交回复',exact:true}).click();
  await c.getByText(reply,{exact:true}).waitFor();
  await a.locator('.ant-drawer').getByText('投递已确认',{exact:true}).waitFor();
  assert.equal(await c.getByText(reply,{exact:true}).count(),1);
  const postReply=async(text)=>{
   const r=await fetch(`${base}/api/inbox/${fixture.case.id}/replies`,{method:'POST',headers:{Authorization:'Bearer fixture-only','Content-Type':'application/json'},body:JSON.stringify({message:text,idempotency_key:crypto.randomUUID()})});
   assert.equal(r.status,200);return r.json();
  };
  const pickConversation=async(title)=>{await c.getByRole('button',{name:'会话',exact:true}).click();await c.getByText(title,{exact:true}).click();};
  const historyB=`${base}/api/v1/conversations/${fixture.conversations[1].id}`;
  // A failed A -> B switch must resume A's event reader even though the active
  // conversation ID has not changed.
  await c.route(historyB,route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"temporary test failure"}'}));
  await Promise.all([c.waitForResponse(r=>r.url()===historyB),pickConversation('API 接入会话')]);
  const recovered='切换失败后继续接收 '+Date.now();await postReply(recovered);
  await c.getByText(recovered,{exact:true}).waitFor();await c.unroute(historyB);
  // A late B history response must not overwrite A after A -> B -> A.
  let releaseB,enteredB;const entered=new Promise(r=>enteredB=r),release=new Promise(r=>releaseB=r);
  const staleText='不应进入网页会话的旧 API 历史';
  await c.route(historyB,async route=>{enteredB();await release;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({id:fixture.conversations[1].id,messages:[{role:'assistant',content:staleText}]})});});
  await pickConversation('API 接入会话');await entered;
  assert.equal(await c.locator('textarea').isDisabled(),true);
  const historyA=`${base}/api/v1/conversations/${fixture.conversations[0].id}`;
  await Promise.all([c.waitForResponse(r=>r.url()===historyA),pickConversation('网页售后会话')]);
  await c.getByText(recovered,{exact:true}).waitFor();
  await Promise.all([c.waitForResponse(r=>r.url()===historyB),Promise.resolve().then(()=>releaseB())]);
  await c.unroute(historyB);
  const afterRace='快速切换后继续接收 '+Date.now();await postReply(afterRace);
  await c.getByText(afterRace,{exact:true}).waitFor();assert.equal(await c.getByText(staleText,{exact:true}).count(),0);
  await c.reload();await c.getByText(reply,{exact:true}).waitFor();
  await Promise.all([c.waitForResponse(r=>r.url().includes('/events?after=')),c.evaluate(()=>window.dispatchEvent(new Event('focus')))]);
  assert.equal(await c.getByText(reply,{exact:true}).count(),1);
  await a.screenshot({path:'/private/tmp/ojf-inbox-flow.png',animations:'disabled',fullPage:true});
  await c.screenshot({path:'/private/tmp/ojf-consumer-reply.png',animations:'disabled',fullPage:true});
  await a.getByRole('button',{name:'关闭',exact:true}).click();
  await a.getByRole('menuitem',{name:'服务',exact:true}).click();
  await a.getByText(fixture.service.name,{exact:true}).first().click();
  await a.getByText('广播与通知',{exact:true}).waitFor();
  await a.locator('#broadcast-recipients').click();
  for(const label of ['[web] 网页售后会话','[api] API 接入会话','[wechat] 微信售后会话']) await a.getByText(label,{exact:true}).click();
  await a.keyboard.press('Escape');
  const notice='三端广播测试 '+Date.now();
  await a.locator('#broadcast-text').fill(notice);
  await a.getByRole('button',{name:'提交广播',exact:true}).click();
  await c.getByText(notice,{exact:true}).waitFor();
  await a.getByText('3 / 3 个会话完成投递',{exact:true}).first().waitFor();
  const apiEvents=await(await fetch(`${base}/api/v1/conversations/${fixture.conversations[1].id}/events`,{headers:{Authorization:'Bearer '+fixture.key}})).json();
  assert(apiEvents.events.some(e=>e.message.content===notice));
  assert.equal((await(await fetch(base+'/__fixture')).json()).wechat_sends,fixture.wechat_sends+1);
  await a.getByText('逐会话投递详情',{exact:true}).first().click();
  await a.screenshot({path:'/private/tmp/ojf-broadcast-flow.png',animations:'disabled',fullPage:true});
  await c.setViewportSize({width:390,height:844});
  await c.screenshot({path:'/private/tmp/ojf-consumer-mobile.png',animations:'disabled',fullPage:true});
  assert(await c.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
  assert.equal(await a.locator('vite-error-overlay').count(),0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({real_api:true,temporary_data:true,real_models:false,real_wechat:false,inbox_reply:true,unread_detail_refresh:true,web_auto_receive:true,reload_dedup:true,failed_switch_recovery:true,stale_history_fenced:true,sending_disabled_during_switch:true,three_channel_notice:true,mobile_no_overflow:true,page_errors:errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

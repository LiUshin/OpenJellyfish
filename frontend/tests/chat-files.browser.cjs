const {join}=require('node:path');
const {tmpdir}=require('node:os');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base=process.env.TEST_BASE_URL || 'http://127.0.0.1:3004';
const tool=(name,args,result,extra={})=>({type:'tool',name,args:JSON.stringify(args),result,done:true,resultCollapsed:true,...extra});
const text=content=>({type:'text',content});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
 try {for (const engine of (process.env.TEST_ENGINES?.split(',') || ['codex','cursor','legacy'])) {
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.addInitScript(()=>{localStorage.setItem('token','fixture-token');localStorage.setItem('jf-color','dark');localStorage.setItem('jf-style','terminal');localStorage.setItem('i18nextLng','zh');});
  const binding={runtime:engine,profile_id:'profile',model:'test-model'};
  const path=engine==='legacy'?'/docs/report.md':'/generated/runtime/session/run/report.md';
  const notes=engine==='legacy'?'/docs/notes.txt':'/generated/runtime/session/run/notes.txt';
  const artifacts=engine==='legacy'?[]:[{id:'report',name:'report.md',path,mime:'text/markdown',size:20},{id:'notes',name:'notes.txt',path:notes,mime:'text/plain',size:12}];
  const command=tool(engine==='legacy'?'run_script':'commandExecution',{command:'python analyse.py',input:'FULL_INPUT_SENTINEL'},'FULL_RESULT_SENTINEL\n42 rows processed',{status:'completed'});
  const edit=engine==='legacy'?tool('edit_file',{file_path:path,old_string:'old title',new_string:'new title'},'1 replacement'):
    tool(engine==='codex'?'fileChange':'edit',{},'Applied complete patch',{status:'completed',changes:[{path:'/private/work/report.md',workspace_path:'report.md',...(engine==='codex'?{diff:'@@ -1 +1 @@\n-old title\n+new title'}:{old_text:'old title',new_text:'new title'})}]});
  const blocks=[text('我先运行分析，然后修改文档。'),command,edit,...(engine==='legacy'?[tool('write_file',{file_path:notes,content:'original notes'},'Saved notes')]:[]),text(engine==='legacy'?`处理完成。<<FILE:${path}>>`:'处理完成。[查看报告](report.md)')];
  const run={id:'run',seq:5,status:'completed',message:'修改报告并导出文件',output:blocks.at(-1).content,blocks,binding,artifacts,pending:null,created_at:Date.now()/1000-15,finished_at:Date.now()/1000};
  const conv={id:'file-review',title:'文件与工具回归',messages:[{role:'user',content:run.message},{role:'assistant',content:run.output,blocks}],...(engine==='legacy'?{}:{runtime_binding:binding,runtime_session_id:'session'})};
  const reads=[],downloads=[],errors=[];
  await context.route('**/api/**',async route=>{
   const url=new URL(route.request().url());const pathname=url.pathname;let value=[];
   if(pathname==='/api/auth/me')value={user_id:'fixture',username:'fixture'};
   else if(pathname==='/api/models')value={models:[{id:'legacy',name:'自挂模型'}],default:'legacy'};
   else if(pathname.startsWith('/api/settings/api-keys'))value={has_llm:true,openai_api_key_configured:true,platform_configured:{openai:true}};
   else if(pathname==='/api/runtime/capabilities')value={enabled:true,available:true};
   else if(pathname==='/api/runtime/profiles')value=engine==='legacy'?[]:[{id:'profile',name:'我的连接',runtime:engine,status:'ready',models:[{id:'test-model',name:'Test Model'}]}];
   else if(pathname==='/api/runtime/preferences')value=engine==='legacy'?{runtime:'deepagents',model:'legacy'}:binding;
   else if(pathname.includes('streaming'))value={streaming:[],interrupted:[]};
   else if(pathname.includes('interrupt'))value={has_interrupt:false};
   else if(pathname.endsWith('/preferences'))value={tz_offset_hours:8};
   else if(pathname==='/api/conversations')value=[conv];
   else if(pathname==='/api/conversations/file-review')value=conv;
   else if(pathname==='/api/runtime/sessions/session')value={id:'session',binding,runs:[run],artifacts};
   else if(pathname==='/api/files')value=[{name:'report.md',path,is_dir:false,size:30},{name:'notes.txt',path:notes,is_dir:false,size:12}];
   else if(pathname==='/api/files/read') {reads.push(url.searchParams.get('path'));value={content:url.searchParams.get('path')===notes?'original notes':'# new title\n\nRIGHT_PANEL_SENTINEL'};}
   else if(pathname==='/api/files/download' || pathname.includes('/artifacts/')){await route.fulfill({contentType:'text/plain',body:'downloaded notes'});return;}
   await route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  });
  const page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));page.on('download',d=>downloads.push(d.suggestedFilename()));
  await page.goto(base);await page.getByText('文件与工具回归',{exact:true}).first().click();
  const header=page.locator('[data-work-phase] > button');await header.waitFor();
  assert.equal(await header.getAttribute('aria-expanded'),'false');
  // Completed file edits remain visible while the process is collapsed.
  await page.locator('[class*="diffRowDel"]').getByText(/old title/).waitFor();
  await page.locator('[class*="diffRowAdd"]').getByText(/new title/).waitFor();
  const jump=page.locator(`[class*="streamFileHeader"] [data-jf-file="${path}"]`);
  await jump.click();
  await page.getByRole('tab',{name:/report.md/}).waitFor();
  await page.locator('.jf-file-md-preview').getByText('RIGHT_PANEL_SENTINEL',{exact:true}).waitFor();
  assert(reads.includes(path));assert.equal(downloads.length,0,'preview must not trigger download');
  assert.equal(await page.getByRole('dialog').count(),0,'uses existing right panel, not modal');
  // The inline answer reference uses the same workspace target.
  assert(await page.locator(`[class*="agentContent"] [data-jf-file="${path}"]`).count()>0);
  await header.click();
  await page.getByRole('button',{name:engine==='legacy'?/运行脚本.*已完成/:/执行命令.*已完成/}).click();
  await page.getByText(/FULL_INPUT_SENTINEL/).waitFor();await page.getByText(/FULL_RESULT_SENTINEL/).waitFor();
  await page.screenshot({path:join(tmpdir(),`jf-restored-files-${engine}.png`)});
  // Generated files preview from chat; downloads live in the right file workspace.
  await page.locator(`[data-jf-file="${notes}"]`).first().click();
  await page.getByRole('tab',{name:/notes.txt/}).waitFor();
  assert.equal(await page.getByRole('region',{name:'本轮文件'}).getByRole('button',{name:/下\s*载/}).count(),0);
  await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'下载当前文件',exact:true}).click()]);
  assert.deepEqual(downloads,['notes.txt']);
  await page.reload();await page.getByText('文件与工具回归',{exact:true}).first().click();await header.waitFor();
  await jump.waitFor();assert.equal(await header.getAttribute('aria-expanded'),'false');
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal overflow');
  const mobilePath=await page.locator('[class*="filePathButton"]').first().boundingBox();
  assert(mobilePath.width>45 && mobilePath.height<60,'file names must not collapse into a vertical column');
  await page.screenshot({path:join(tmpdir(),`jf-restored-files-${engine}-mobile.png`)});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({engine,collapsed_diff:true,full_tool_details:true,right_preview:true,inline_file_links:true,download_in_right_workspace:true,history_reload:true,mobile:true,page_errors:0}));
  await context.close();
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const ts = require('typescript');

// These display helpers have no runtime imports. Compile with the project's
// existing TypeScript dependency so tests need no separate test framework.
function load(name) {
  const source = readFileSync(resolve(__dirname, '../src/pages/Chat/utils', name + '.ts'), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const exports = {};
  new Function('exports', outputText)(exports);
  return exports;
}
const { responsePhase, splitResponse, toolOutcome } = load('responsePresentation');
const { plainPreview, truncatePreview, answerPreview, conversationPreviews } = load('userQueryPreview');
const text = content => ({ type: 'text', content });
const tool = result => ({ type: 'tool', name: 'read_file', args: 'SECRET_ARGUMENT', result, done: true, resultCollapsed: true });

test('active process preserves order; only the text after the last activity becomes the answer', () => {
  const blocks = [text('checking'), tool('SECRET_RESULT'), text('intermediate update'), tool('done'), text('final answer')];
  assert.deepEqual(splitResponse(blocks, true).process.map(x => x.block), blocks);
  const settled = splitResponse(blocks, false);
  assert.deepEqual(settled.process.map(x => x.block), blocks.slice(0, 4));
  assert.deepEqual(settled.answer.map(x => x.block), [blocks[4]]);
  assert.equal(settled.answer[0].index, 4);
});
test('text-only responses remain directly readable', () => {
  const blocks = [text('answer')];
  for (const working of [true, false]) {
    const result = splitResponse(blocks, working);
    assert.equal(result.hasActivity, false);
    assert.deepEqual(result.answer.map(x => x.block), blocks);
  }
});
test('reasoning and hidden service subagents never enter the visible process', () => {
  const blocks = [{ type: 'thinking', content: 'SECRET_REASONING' }, { type: 'subagent', content: 'SECRET_SUBAGENT' }, text('public')];
  const result = splitResponse(blocks, true, true);
  assert.equal(result.process.length, 0);
  assert.deepEqual(result.answer.map(x => x.block), [text('public')]);
});
test('approval pauses and resume use distinct phases; queued/starting/running share one phase', () => {
  assert.deepEqual(['queued','starting','running','cancel_requested'].map(responsePhase), Array(4).fill('working'));
  assert.equal(responsePhase('waiting_approval'), 'waiting');
  assert.deepEqual(['completed','failed','cancelled'].map(responsePhase), Array(3).fill('settled'));
});
test('failed, denied and incomplete tools are not reported as successful', () => {
  assert.equal(toolOutcome(tool('失败')), 'failed');
  assert.equal(toolOutcome(tool('Error: file not found')), 'failed');
  assert.equal(toolOutcome(tool('已拒绝')), 'stopped');
  assert.equal(toolOutcome(tool('已取消')), 'stopped');
  assert.equal(toolOutcome(tool('本轮已结束；未收到工具完成事件')), 'unknown');
});
test('QA previews exclude reasoning/tool payloads and avoid falling back to internal content', () => {
  const blocks = [{ type:'thinking',content:'SECRET_REASONING' }, text('checking'), tool('SECRET_RESULT')];
  assert.equal(answerPreview(blocks, 'SECRET_FALLBACK'), '');
  assert.equal(answerPreview([...blocks, text('**public** [answer](https://example.test)')]), 'public answer');
  assert.equal(plainPreview('<think>SECRET</think>Visible'), 'Visible');
  assert.equal(plainPreview('Visible<think>unfinished SECRET'), 'Visible');
});
test('preview truncation preserves astral characters', () => {
  assert.equal(truncatePreview('🪼你好🪼再见', 4), '🪼你好🪼…');
});
test('each question gets its answer; current streaming output only updates the latest turn', () => {
  const messages = [{role:'user',content:'Q1'},{role:'assistant',content:'A1'},{role:'user',content:'Q2'}];
  const result = conversationPreviews(messages, [tool('SECRET'), text('A2')]);
  assert.deepEqual(result.map(({question,answer})=>({question,answer})), [{question:'Q1',answer:'A1'},{question:'Q2',answer:'A2'}]);
});


const { appendRuntimeEvent } = load('runtimeBlocks');
const { runtimePresentation } = load('runtimePresentation');
test('runtime details survive partial updates, completion, and interrupted snapshots', () => {
  let blocks=[];
  const emit=payload=>{blocks=appendRuntimeEvent(blocks,{type:'tool',payload});};
  emit({item_id:'a',kind:'execute',command:'x'.repeat(9000),status:'inProgress'});
  emit({item_id:'a',input:{command:'real command'}});
  emit({item_id:'a',command:'friendly title',result_delta:'partial'});
  emit({item_id:'a',result:'full diagnostic output',exit_code:2,status:'failed'});
  assert.equal(JSON.parse(blocks[0].args).command,'real command');
  assert.equal(blocks[0].result,'full diagnostic output');
  assert.equal(toolOutcome(blocks[0]),'failed');
  emit({item_id:'b',result_delta:'preserve this'});
  blocks=appendRuntimeEvent(blocks,{type:'cancelled',payload:{}});
  assert.equal(blocks[1].result,'preserve this');assert.equal(toolOutcome(blocks[1]),'unknown');
});
test('archived runtime paths become preview links without resolving unrelated host paths', () => {
  const files=[{id:'1',name:'nested/report.md',path:'/generated/runtime/s/r/nested/report.md'}];
  const blocks=[{...tool('complete'),changes:[{path:'/private/work/nested/report.md',workspace_path:'nested/report.md',diff:'-old\n+new'}]},text('[report](nested/report.md#summary) and `nested/report.md` and `/other/report.md`\n```txt\n`nested/report.md`\n```')];
  const view=runtimePresentation(blocks,files);
  assert.equal(view.blocks[0].changes[0].preview_path,files[0].path);
  assert.equal(view.blocks.length,blocks.length);
  assert(view.blocks[1].content.includes('<<FILE:'+files[0].path+'#summary>>'));
  assert(view.blocks[1].content.includes('`/other/report.md`'));
  assert(view.blocks[1].content.includes('```txt\n`nested/report.md`\n```'));
  assert.equal(blocks[0].changes[0].preview_path,undefined,'do not mutate durable state');
});
test('file renderer recognizes namespaced edits and native diffs', () => {
  const { isFileTool }=load('responsePresentation');
  assert(isFileTool({...tool('done'),name:'mcp__jellyfish__edit_file'}));
  assert(isFileTool({...tool('done'),name:'edit',changes:[{path:'a.md'}]}));
  assert(!isFileTool(tool('done')));
});


test('runtime file references keep incomplete code fences literal and preserve image syntax', () => {
  const files=[{id:'image',name:'plot.png',path:'/generated/runtime/s/r/plot.png'}];
  const view=runtimePresentation([text('![plot](plot.png)\n```txt\n`plot.png`')],files);
  assert.equal(view.blocks[0].content,'<<FILE:/generated/runtime/s/r/plot.png>>\n```txt\n`plot.png`');
});


test('late archive delivery preserves inline files and appends only missing files after the answer', () => {
  const report={id:'r',name:'report.md',path:'/generated/runtime/s/r/report.md'};
  const notes={id:'n',name:'notes.txt',path:'/generated/runtime/s/r/notes.txt'};
  const body=text('说明在前\n\n<<FILE:'+report.path+'>>\n\n总结在后');
  const blocks=[tool('done'),body];
  assert.deepEqual(runtimePresentation(blocks,[]).blocks,blocks);
  const archived=runtimePresentation(blocks,[report,notes]);
  assert.deepEqual(archived.blocks.slice(0,blocks.length),blocks);
  assert.equal(archived.blocks.at(-1).content,'<<FILE:'+notes.path+'>>');
  assert.equal(archived.blocks[1],body,'unchanged body keeps its reference during archive updates');
  assert.deepEqual(runtimePresentation(JSON.parse(JSON.stringify(blocks)),[report,notes]).blocks,archived.blocks,'history has identical order');
  assert.deepEqual(runtimePresentation(archived.blocks,[report,notes]).blocks,archived.blocks,'replay does not duplicate links');
});

test('inline aliases and image tags suppress duplicate fallback files; code examples do not', () => {
  const files=['report.md','plot.png','example.txt'].map((name,i)=>({id:String(i),name,path:'/generated/runtime/s/r/'+name}));
  const body=text('[report](report.md#summary)\n\n![plot](plot.png)\n\n```txt\n`example.txt`\n```');
  const view=runtimePresentation([body],files);
  assert.equal(view.blocks.length,2);
  assert.equal(view.blocks[1].content,'<<FILE:'+files[2].path+'>>');
  assert(view.blocks[0].content.includes('#summary>>'));
  assert(view.blocks[0].content.includes('<<FILE:'+files[1].path+'>>'));
});

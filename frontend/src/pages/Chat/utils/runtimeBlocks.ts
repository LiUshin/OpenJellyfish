import type { StreamBlock, ToolBlock } from '../types';
import type { RuntimeEvent } from '../../../services/runtime';

type Tool = ToolBlock & { event_key?: string };

/** Matches the durable server projection; preserves unchanged block references. */
export function appendRuntimeEvent(previous: StreamBlock[], event: RuntimeEvent): StreamBlock[] {
  const { type, payload } = event;
  const blocks = [...previous];
  if (type === 'notice') {
    blocks.push({ type: 'text', content: '\n\n提示：' + (payload.message || '') });
  } else if (type === 'text_delta') {
    const tail = blocks[blocks.length - 1];
    if (tail?.type === 'text') blocks[blocks.length - 1] = { ...tail, content: tail.content + (payload.text || '') };
    else if (payload.text) blocks.push({ type: 'text', content: payload.text });
  } else if (type === 'tool' || type === 'business_tool') {
    const key = payload.item_id || payload.name || `tool-${blocks.length}`;
    let index = -1;
    for (let i = blocks.length - 1; i >= 0; i--) {
      if (blocks[i].type === 'tool' && (blocks[i] as Tool).event_key === key) { index = i; break; }
    }
    let block: Tool | undefined = index < 0 ? undefined : blocks[index] as Tool;
    if (block && type === 'business_tool' && payload.status === 'running' && block.done) { block = undefined; index = -1; }
    block = block ? { ...block } : { type: 'tool', event_key: key, name: payload.name || (payload.kind === 'other' ? payload.command : undefined) || payload.kind || '工具', args: '', result: '', done: false, resultCollapsed: true };
    block.name = ({ webSearch: '网页搜索', search: '网页搜索', imageGeneration: '生成图片', fetch: '读取网页' } as Record<string, string>)[block.name] || block.name;
    if (payload.name) block.name = payload.name;
    const display = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    if ('input' in payload) { block.args = display(payload.input); block.has_input = true; }
    else if (payload.command && !block.has_input) block.args = payload.command;
    if ('result' in payload) block.result = display(payload.result);
    if (payload.result_delta) block.result += payload.result_delta;
    if (payload.changes) block.changes = payload.changes;
    if ('exit_code' in payload) block.exit_code = payload.exit_code;
    if (payload.status) block.status = payload.status;
    const states: Record<string, string> = { completed: '已完成', failed: '失败', cancelled: '已取消', declined: '已拒绝' };
    if (payload.status && states[payload.status]) { block.done = true; block.result ||= states[payload.status]; }
    if (index < 0) blocks.push(block); else blocks[index] = block;
  } else if (['completed', 'failed', 'cancelled'].includes(type)) {
    return blocks.map(block => block.type === 'tool' && !block.done ? { ...block, done: true, status: 'unknown', result: block.result || '本轮已结束；未收到工具完成事件' } : block);
  }
  return blocks;
}

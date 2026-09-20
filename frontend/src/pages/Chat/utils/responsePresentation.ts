import type { StreamBlock, ToolBlock } from '../types';

export type ResponsePhase = 'working' | 'waiting' | 'settled';
export function responsePhase(status: string): ResponsePhase {
  if (status === 'waiting_approval') return 'waiting';
  return ['completed', 'cancelled', 'failed'].includes(status) ? 'settled' : 'working';
}

const LABELS: Record<string, string> = {
  read_file: '读取文件', write_file: '写入文件', edit_file: '编辑文件',
  ls: '浏览文件', glob: '查找文件', grep: '搜索文件', list_files_sorted: '整理文件',
  read_memory: '读取记忆', query_memory: '检索记忆', save_memory: '保存记忆',
  list_documents: '浏览文档目录', read_document: '读取文档',
  web_search: '搜索网页', webSearch: '搜索网页', search: '搜索网页',
  fetch_url: '读取网页', fetch: '读取网页', tavily_search: '搜索网页',
  generate_image: '生成图片', imageGeneration: '生成图片',
  generate_speech: '生成语音', generate_video: '生成视频',
  run_script: '运行脚本', commandExecution: '执行命令', shell: '执行命令',
  exec_command: '执行命令', fileChange: '修改文件',
  write_todos: '更新计划', propose_plan: '拟定方案', task: '执行子任务',
  create_subagent: '执行子任务', scheduled_task: '定时任务',
};

export function canonicalToolName(name: string): string {
  return name.split(/__|\./).pop()!.replace(/^jellyfish_/, '');
}

export function isFileTool(block: StreamBlock): block is ToolBlock {
  return block.type === 'tool' && (['write_file', 'edit_file'].includes(canonicalToolName(block.name)) || !!block.changes?.length);
}

export function toolLabel(name: string): string {
  const short = canonicalToolName(name);
  return LABELS[name] || LABELS[short] || name || '工具';
}

/** Runtime persists terminal outcomes in result; do not turn a rejected or
 * incomplete call into a green success check when restoring history. */
export function toolOutcome(block: ToolBlock): 'running' | 'done' | 'failed' | 'stopped' | 'unknown' {
  if (!block.done) return 'running';
  if (block.status === 'failed') return 'failed';
  if (['cancelled', 'declined'].includes(block.status || '')) return 'stopped';
  if (block.status === 'unknown') return 'unknown';
  if (block.status === 'completed') return 'done';
  if (/^(error\b|failed\b|failure\b|✗|❌|失败|出错)/i.test(block.result.trim())) return 'failed';
  if (['已取消', '已拒绝'].includes(block.result)) return 'stopped';
  if (block.result === '本轮已结束；未收到工具完成事件') return 'unknown';
  return 'done';
}

export function splitResponse(blocks: StreamBlock[], working: boolean, hideSubagents = false) {
  const visible = blocks.map((block, index) => ({ block, index })).filter(({ block }) =>
    block.type !== 'thinking' && block.type !== 'auto_approve' && !(hideSubagents && block.type === 'subagent'));
  let lastActivity = -1;
  visible.forEach(({ block }, i) => { if (block.type === 'tool' || block.type === 'subagent') lastActivity = i; });
  return {
    hasActivity: lastActivity >= 0,
    process: lastActivity < 0 ? [] : visible.slice(0, working ? undefined : lastActivity + 1),
    answer: lastActivity < 0 ? visible : working ? [] : visible.slice(lastActivity + 1),
  };
}

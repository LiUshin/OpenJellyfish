/**
 * Service-side tool block renderer — 友好状态条 + 不展示 args/result。
 *
 * 与 admin 的 ToolIndicator（真实工具名 + 可展开 args/result）故意不一致：
 * - admin 需要看完整工具调用细节用于调试
 * - consumer 只需要"agent 在做事"的轻量反馈，且不应看到内部能力名（隐私）
 *
 * 工具名通过白名单 _TOOL_LABELS 友好化，未列入的统一显示「思考中…」，
 * 避免泄露内部能力（如 send_message / contact_admin / memory subagent 等）。
 */

import type { ToolBlock } from '../pages/Chat/types';
import styles from './serviceChat.module.css';

/**
 * 工具名 → consumer-facing 友好文案映射。
 *
 * 实际工具名以 `app/services/consumer_agent.py` 与 deepagents 默认 toolset 为准；
 * 保持白名单与后端同步（修改 consumer 工具时务必同步这里），未列入的将兜底为「思考中…」。
 */
const TOOL_LABELS: Record<string, string> = {
  // ── 文件系统（consumer_agent.py 自定义 + deepagents 内置） ────────
  ls: '正在查找资料…',
  glob: '正在查找资料…',
  grep: '正在搜索资料…',
  read_file: '正在阅读文档…',
  write_file: '正在保存文件…',
  edit_file: '正在编辑文件…',

  // ── 计划/子任务（deepagents 内置） ────────────────────────────────
  write_todos: '正在制定计划…',
  task: '正在调度子任务…',
  create_subagent: '正在调度子任务…',

  // ── 脚本执行 ─────────────────────────────────────────────────────
  run_script: '正在运行脚本…',

  // ── AI 生成 ─────────────────────────────────────────────────────
  generate_image: '正在生成图片…',
  generate_speech: '正在合成语音…',
  generate_video: '正在生成视频…',

  // ── 联网检索 ─────────────────────────────────────────────────────
  web_search: '正在联网检索…',
  fetch_url: '正在读取网页…',
  tavily_search: '正在联网检索…',

  // ── 记忆 ────────────────────────────────────────────────────────
  query_memory: '正在回忆上下文…',
  save_memory: '正在记录笔记…',

  // ── 计划/审批（HITL 类，但 consumer 路径理论上不会触发；列出兜底） ──
  propose_plan: '正在拟定方案…',
  ask_user: '正在请求确认…',
};

function friendlyLabel(name: string): string {
  if (!name) return '思考中…';
  if (TOOL_LABELS[name]) return TOOL_LABELS[name];
  // 同名前缀兜底：例如 langgraph 内置可能带命名空间前缀
  for (const k of Object.keys(TOOL_LABELS)) {
    if (name.endsWith(`__${k}`) || name.endsWith(`.${k}`)) return TOOL_LABELS[k];
  }
  return '思考中…';
}

interface Props {
  block: ToolBlock;
}

export default function ServiceToolBadge({ block }: Props) {
  return (
    <div
      className={`${styles.toolBadge} ${block.done ? styles.toolBadgeDone : ''}`}
      // data-tool 仅用于调试时定位；不展示给消费者
      data-tool={block.name || 'unknown'}
    >
      <span className={styles.toolBadgeDot} aria-hidden />
      <span className={styles.toolBadgeText}>{friendlyLabel(block.name)}</span>
    </div>
  );
}

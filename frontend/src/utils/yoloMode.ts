/**
 * YOLO 模式（admin 端）：本地开关。
 *
 * 打开后所有 admin 聊天会话发请求时带 `yolo=true`：
 *   - 后端 _stream_agent 不设循环上限地自动批准所有 HITL interrupt
 *     （write_file / edit_file / propose_plan），仅每 50 次循环打一条 warning 日志。
 *   - 前端不再弹 ApprovalCard，也不再向消息流插入显眼的 auto_approve 徽章。
 *   - SSE `auto_approve` 事件仅用于驱动 Chat 输入区底部的不显眼小 tag「yolo」，
 *     提示「本会话已发生过 YOLO 自动批准」。tag 在浏览器刷新或切换会话后消失。
 *
 * 仅作用于 admin 端（service / consumer 路径默认无 HITL，开关无影响）。
 *
 * 持久化：localStorage，全局事件 'yolo-mode-changed' 让其他面板同步刷新。
 */
export const YOLO_KEY = 'yolo_mode_admin';
export const YOLO_EVENT = 'yolo-mode-changed';

export function getYoloMode(): boolean {
  try {
    return localStorage.getItem(YOLO_KEY) === '1';
  } catch {
    return false;
  }
}

export function setYoloMode(on: boolean): void {
  try {
    localStorage.setItem(YOLO_KEY, on ? '1' : '0');
    window.dispatchEvent(new Event(YOLO_EVENT));
  } catch {
    /* ignore */
  }
}

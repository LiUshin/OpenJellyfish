import type { Message } from '../../../types';

/** 从用户消息提取纯文本预览（截断由调用方决定）。 */
export function userQueryPreview(msg: Message): string {
  return msg.content.replace(/\s+/g, ' ').trim();
}

export function truncatePreview(text: string, maxLen = 100): string {
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen)}…`;
}

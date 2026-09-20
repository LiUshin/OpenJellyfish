import type { Message } from '../../../types';
import type { StreamBlock } from '../types';

/** Preview only display text, never reasoning, tool inputs, or tool results. */
export function plainPreview(text: string): string {
  return text
    .replace(/<think(?:ing)?>[\s\S]*?(?:<\/think(?:ing)?>|$)/gi, '')
    .replace(/```[^\n]*\n?/g, '').replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/<[^>]+>/g, '')
    .replace(/(^|\n)\s{0,3}(?:#{1,6}\s|>\s|[-*+]\s|\d+\.\s)/g, '$1')
    .replace(/[*_`~]/g, '').replace(/\s+/g, ' ').trim();
}
export function userQueryPreview(msg: Message): string { return plainPreview(msg.content); }
export function truncatePreview(text: string, maxLen = 100): string {
  const chars = Array.from(text);
  return chars.length <= maxLen ? text : `${chars.slice(0, maxLen).join('')}…`;
}

type PreviewBlock = { type: string; content?: string };
export function answerPreview(blocks?: readonly PreviewBlock[], fallback = ''): string {
  if (!blocks?.length) return plainPreview(fallback);
  let lastActivity = -1;
  blocks.forEach((b, i) => { if (b.type === 'tool' || b.type === 'subagent') lastActivity = i; });
  return plainPreview(blocks.slice(lastActivity + 1).filter(b => b.type === 'text').map(b => b.content || '').join('\n'));
}

export function conversationPreviews(messages: Message[], streamBlocks?: StreamBlock[]) {
  const items: { id: string; index: number; question: string; answer: string }[] = [];
  messages.forEach((message, index) => {
    if (message.role === 'user') items.push({ id: String(index), index, question: userQueryPreview(message), answer: '' });
    else if (message.role === 'assistant' && items.length) {
      const answer = answerPreview(message.blocks, message.content);
      if (answer) items[items.length - 1].answer = answer;
    }
  });
  if (streamBlocks?.length && items.length) items[items.length - 1].answer = answerPreview(streamBlocks);
  return items;
}

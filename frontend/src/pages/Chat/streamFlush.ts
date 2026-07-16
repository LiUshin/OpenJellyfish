/**
 * 流式 blocks → React state 的指纹 flush。
 *
 * SSE 回调原地 mutate（content += token / args += delta），若只
 * `setBlocks([...refs])`，memo 子组件（StreamTextBlock 等）会因
 * block 引用未变而 bail out，表现为「流式时不渲染、结束才整段出现」。
 *
 * 指纹变了 → cloneBlock 换新引用；未变 → 复用上一帧引用（已完成 block 零重渲）。
 * admin `streamContext` 与 service `streamHandler` 必须共用此逻辑。
 */

import type { StreamBlock } from './types';

export function blockFingerprint(b: StreamBlock): string {
  switch (b.type) {
    case 'thinking':
      return `t:${b.content.length}:${b.collapsed ? 1 : 0}`;
    case 'text':
      return `x:${b.content.length}`;
    case 'tool':
      return `o:${b.name}:${b.done ? 1 : 0}:${b.args.length}:${b.result.length}:${b.resultCollapsed ? 1 : 0}`;
    case 'subagent': {
      let tlLen = 0;
      for (const e of b.timeline) {
        tlLen += (e.content?.length ?? 0) + (e.toolName?.length ?? 0) + (e.toolDone ? 1 : 0);
      }
      return `s:${b.done ? 1 : 0}:${b.status}:${b.content.length}:${b.tools.length}:${b.timeline.length}:${tlLen}:${b.collapsed ? 1 : 0}`;
    }
    case 'auto_approve':
      return `a:${b.count}`;
    default:
      return '?';
  }
}

/** 浅克隆一个 block（含内部数组），产生新引用让 memo 感知「内容变了」。 */
export function cloneBlock(b: StreamBlock): StreamBlock {
  switch (b.type) {
    case 'subagent':
      return {
        ...b,
        tools: b.tools.map((t) => ({ ...t })),
        timeline: b.timeline.map((e) => ({ ...e })),
      };
    case 'auto_approve':
      return { ...b, actions: [...b.actions] };
    default:
      return { ...b };
  }
}

/**
 * 把 raw blocks 提交为「指纹感知」的新数组，供 setState 使用。
 * 返回值同时作为下一帧的 emitted 缓存（调用方应写回 ref）。
 */
export function buildFingerprintedBlocks(
  raw: StreamBlock[],
  prevEmitted: StreamBlock[],
  prevFp: string[],
): { next: StreamBlock[]; nextFp: string[] } {
  const next: StreamBlock[] = new Array(raw.length);
  const nextFp: string[] = new Array(raw.length);

  for (let i = 0; i < raw.length; i++) {
    const fp = blockFingerprint(raw[i]);
    nextFp[i] = fp;
    if (i < prevFp.length && prevFp[i] === fp && prevEmitted[i]) {
      next[i] = prevEmitted[i];
    } else {
      next[i] = cloneBlock(raw[i]);
    }
  }
  return { next, nextFp };
}

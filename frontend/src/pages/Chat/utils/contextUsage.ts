import type { Message } from '../../../types';
import type { RuntimeChoice, RuntimeRun } from '../../../services/runtime';

export interface ContextUsageSnapshot {
  used_tokens: number | null;
  limit_tokens: number | null;
  source: 'codex';
}

export interface ConversationTextUsage {
  messages: number;
  tools: number;
  messageCount: number;
  toolCount: number;
  attachmentCount: number;
}

/** A display-only heuristic, never used to infer model occupancy or billing. */
export function estimateTextTokens(text: string): number {
  let weight = 0;
  for (const char of text) weight += char.codePointAt(0)! <= 127 ? .25 : 1;
  return Math.ceil(weight);
}

type ToolText = { args?: string; result?: string };
const toolTextCache = new WeakMap<ToolText, number>();

function estimateToolText(tool: ToolText): number {
  const cached = toolTextCache.get(tool);
  if (cached !== undefined) return cached;
  const estimate = estimateTextTokens(tool.args || '') + estimateTextTokens(tool.result || '');
  // SSE reducers replace only the changed block. Stable tool blocks survive
  // new run snapshots, so later text deltas need not rescan large results.
  // Weak keys release the estimate with its block; no strings are retained.
  toolTextCache.set(tool, estimate);
  return estimate;
}

export function contextCapacity(snapshot?: ContextUsageSnapshot | null) {
  const nonnegative = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  const used = nonnegative(snapshot?.used_tokens) ? snapshot!.used_tokens : null;
  const limit = nonnegative(snapshot?.limit_tokens) && snapshot!.limit_tokens! > 0 ? snapshot!.limit_tokens : null;
  return { used, limit, free: used !== null && limit !== null ? Math.max(0, limit - used) : null,
    percent: used !== null && limit !== null ? Math.round(used / limit * 100) : null };
}

export function summarizeMessages(messages: Message[]): ConversationTextUsage {
  const summary = { messages: 0, tools: 0, messageCount: 0, toolCount: 0, attachmentCount: 0 };
  for (const message of messages) {
    summary.messages += estimateTextTokens(message.content || '');
    summary.messageCount++;
    summary.attachmentCount += message.attachments?.length || 0;
    // tool_calls and blocks can be two representations of the same tool.
    const tools = message.tool_calls?.length ? message.tool_calls : message.blocks?.filter(block => block.type === 'tool') || [];
    for (const tool of tools) {
      summary.tools += estimateToolText(tool);
      summary.toolCount++;
    }
  }
  return summary;
}

const runCache = new WeakMap<RuntimeRun, ConversationTextUsage>();
export function summarizeRuns(runs: RuntimeRun[]): ConversationTextUsage {
  const summary = { messages: 0, tools: 0, messageCount: 0, toolCount: 0, attachmentCount: 0 };
  for (const run of runs) {
    let item = runCache.get(run);
    if (!item) {
      const tools = run.blocks?.filter(block => block.type === 'tool') || [];
      item = { messages: estimateTextTokens(run.message || '') + estimateTextTokens(run.output || ''),
        tools: tools.reduce((sum, tool) => sum + estimateToolText(tool), 0),
        messageCount: Number(!!run.message) + Number(!!run.output), toolCount: tools.length, attachmentCount: run.attachments?.length || 0 };
      runCache.set(run, item);
    }
    for (const key of Object.keys(summary) as (keyof ConversationTextUsage)[]) summary[key] += item[key];
  }
  return summary;
}

/** The newest run owns the snapshot. Never borrow a different model's window. */
export function latestContextSnapshot(runs: RuntimeRun[], selectedBinding?: RuntimeChoice): ContextUsageSnapshot | null {
  const newest = runs[runs.length - 1];
  if (selectedBinding && (!newest || newest.binding?.runtime !== selectedBinding.runtime
    || newest.binding?.profile_id !== selectedBinding.profile_id
    || newest.binding?.model !== selectedBinding.model)) return null;
  return newest?.context_usage ?? null;
}

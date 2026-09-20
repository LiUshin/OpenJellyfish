import { memo, useId, useMemo, useRef, useState, type ReactNode, type ComponentType } from 'react';
import { CaretDown, Wrench } from '@phosphor-icons/react';
import type { StreamBlock, TextBlock, ToolBlock } from '../types';
import type { PlanStep } from '../../../stores/streamContext';
import { renderMarkdown, renderStreamingMarkdown } from '../markdown';
import { responsePhase, splitResponse, toolLabel, toolOutcome, isFileTool, canonicalToolName } from '../utils/responsePresentation';
import ToolIndicator from './ToolIndicator';
import StreamingFilePreview from './StreamingFilePreview';
import FileChangeCard from './FileChangeCard';
import SubagentCard from './SubagentCard';
import ScheduledTaskCard from './ScheduledTaskCard';
import PlanTracker from './PlanTracker';
import WorkProgress from './WorkProgress';
import styles from '../chat.module.css';
import work from './workProgress.module.css';

export interface ToolRendererProps { block: ToolBlock }
export interface ResponseBodyProps {
  blocks: StreamBlock[];
  isStreaming: boolean;
  status?: string;
  startedAt?: number;
  finishedAt?: number;
  toolRenderer?: ComponentType<ToolRendererProps>;
  hideSubagents?: boolean;
  scheduledTaskFriendlyMode?: boolean;
  planSteps?: PlanStep[] | null;
}

const Text = memo(function Text({ block, streaming, progress = false }: { block: TextBlock; streaming: boolean; progress?: boolean }) {
  const html = useMemo(() => streaming ? renderStreamingMarkdown(block.content) : renderMarkdown(block.content), [block, streaming]);
  return <div className={progress ? `${styles.messageContent} ${styles.agentContent} ${work.progressText}` : `${styles.messageContent} ${styles.agentContent} ${streaming ? styles.streamingCursor : ''}`}
    dangerouslySetInnerHTML={{ __html: html }} />;
});

type IndexedBlock = { block: StreamBlock; index: number };
function ToolGroup({ entries, working, render }: { entries: IndexedBlock[]; working: boolean; render: (entry: IndexedBlock) => ReactNode }) {
  const [state, setState] = useState({ working, open: working });
  if (state.working !== working) setState({ working, open: working });
  const open = state.working === working ? state.open : working;
  const id = useId();
  const visited = useRef(open);
  if (open) visited.current = true;
  const completed = entries.filter(({ block }) => block.type === 'tool' && toolOutcome(block) === 'done').length;
  return <div className={work.entry}>
    <button type="button" className={work.groupSummary} aria-expanded={open} aria-controls={id} onClick={() => setState({ working, open: !open })}>
      <Wrench size={13} /><span>工具调用</span><span className={work.count}>{completed}/{entries.length} 完成</span>
      <CaretDown size={12} className={open ? work.caretOpen : work.caret} />
    </button>
    <div id={id} hidden={!open} className={work.groupBody}>{visited.current && entries.map(entry => render(entry))}</div>
  </div>;
}

function AssistantResponseBody({ blocks, isStreaming, status = isStreaming ? 'running' : 'completed', startedAt, finishedAt,
  toolRenderer, hideSubagents = false, scheduledTaskFriendlyMode = false, planSteps }: ResponseBodyProps) {
  const working = responsePhase(status) === 'working';
  const { process, answer, hasActivity } = useMemo(() => splitResponse(blocks, working, hideSubagents), [blocks, working, hideSubagents]);
  const ToolComp = toolRenderer ?? ToolIndicator;
  const firstPlan = planSteps?.length ? blocks.findIndex(b => b.type === 'tool' && ['write_todos', 'propose_plan'].includes(b.name)) : -1;
  function render(entry: IndexedBlock, progress = true) {
    const { index } = entry;
    const block = entry.block.type === 'tool' && !entry.block.done && responsePhase(status) === 'settled'
      ? { ...entry.block, done: true, status: 'unknown', result: entry.block.result || '本轮已结束；未收到工具完成事件' }
      : entry.block;
    const streaming = isStreaming && working && index === blocks.length - 1;
    const key = `${block.type}-${index}`;
    if (block.type === 'text') return <Text key={key} block={block} streaming={streaming} progress={progress} />;
    if (block.type === 'subagent') return <SubagentCard key={key} block={block} />;
    if (block.type !== 'tool') return null;
    if (firstPlan >= 0 && ['write_todos', 'propose_plan'].includes(block.name)) return index === firstPlan ? <PlanTracker key={key} steps={planSteps!} defaultCollapsed /> : null;
    if (block.name === 'scheduled_task') return <ScheduledTaskCard key={key} block={block} friendlyMode={scheduledTaskFriendlyMode} />;
    if (block.changes?.length && toolRenderer) return <ToolComp key={key} block={block} />;
    if (isFileTool(block)) return <div key={key}>
      {block.changes?.length && !toolRenderer ? <FileChangeCard block={block} />
        : ['write_file', 'edit_file'].includes(canonicalToolName(block.name))
          ? <StreamingFilePreview block={{ ...block, name: canonicalToolName(block.name) }} isStreaming={streaming} /> : null}
      {!toolRenderer && <ToolIndicator block={block} />}
    </div>;
    return <ToolComp key={key} block={block} />;
  }
  // Adjacent calls only: text between tool batches keeps its original place.
  const groups: IndexedBlock[][] = [];
  // File/edit results stay directly accessible after completion. During a run
  // they remain in order, outside the generic tool-group disclosure.
  const settledFiles = working ? [] : process.filter(entry => isFileTool(entry.block));
  const groupable = (entry: IndexedBlock) => entry.block.type === 'tool' && !isFileTool(entry.block);
  process.filter(entry => !settledFiles.includes(entry)).forEach(entry => {
    const previous = groups[groups.length - 1];
    if (groupable(entry) && previous && groupable(previous[0]) && previous[previous.length - 1].index + 1 === entry.index) previous.push(entry);
    else groups.push([entry]);
  });
  const runningTool = [...process].reverse().find(({ block }) => block.type === 'tool' && !block.done)?.block as ToolBlock | undefined;
  const count = process.filter(({ block }) => block.type === 'tool' || block.type === 'subagent').length;
  const labels: Record<string, string> = { queued: '等待连接', starting: '正在准备会话', waiting_approval: '等待你的确认', completed: '处理完成', cancelled: '已停止，进展已保留', failed: '执行失败，进展已保留' };
  const label = labels[status] || (runningTool ? (toolRenderer ? '正在使用工具' : `正在${toolLabel(runningTool.name)}`) : hasActivity ? '正在整理回答' : '正在准备回答');
  const showProgress = hasActivity || !answer.length || responsePhase(status) === 'waiting';
  return <>
    {showProgress && <WorkProgress status={status} label={label} count={count} startedAt={startedAt} finishedAt={finishedAt}>
      {groups.length ? groups.map(entries => groupable(entries[0]) && entries.length > 1
        ? <ToolGroup key={entries[0].index} entries={entries} working={working} render={render} />
        : <div key={entries[0].index} className={work.entry}>{render(entries[0])}</div>)
        : <div className={work.note}>{status === 'waiting_approval' ? '确认后将继续执行。' : working ? '等待模型返回可显示的进展。' : settledFiles.length ? '文件修改已保留在下方。' : '本轮没有可显示的执行记录。'}</div>}
    </WorkProgress>}
    {settledFiles.map(entry => render(entry, false))}
    {answer.length > 0 && <>
      {['cancelled', 'failed'].includes(status) && <div className={work.partial}>本轮未完成 · 以下为已生成的内容</div>}
      {answer.map(entry => render(entry, false))}
    </>}
  </>;
}
export default memo(AssistantResponseBody);

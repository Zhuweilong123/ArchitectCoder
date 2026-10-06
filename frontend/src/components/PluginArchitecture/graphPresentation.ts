import type { PlanEdge, PlanNode } from './pluginGraph';

export const STAGE_LABELS: Record<string, [string, string]> = {
  initialize: ['初始化', 'Initialize'], prepare: ['任务准备', 'Prepare'], model_before: ['模型调用前', 'Model before'],
  model_after: ['模型调用后', 'Model after'], finalize: ['执行收尾', 'Finalize'],
  agent_initialize: ['Agent 初始化', 'Agent initialization'], context_prepare: ['上下文准备', 'Context preparation'],
  skill_read: ['技能读取', 'Skill read'], memory_reinforce: ['记忆强化', 'Memory reinforcement'], task_archive: ['任务归档', 'Task archive'],
  review_after: ['审核结果', 'Review result'], background_before: ['后台任务开始', 'Background start'], background_after: ['后台任务结束', 'Background end'],
  orchestration_prepare: ['编排准备', 'Orchestration preparation'], trace_initialize: ['Trace 初始化', 'Trace initialization'],
  orchestration_execute: ['编排探索', 'Orchestration exploration'],
  trace_query: ['Trace 查询', 'Trace query'], trace_replay: ['Trace 回放', 'Trace replay'],
  evaluation_query: ['评测查询', 'Evaluation query'], evaluation_run: ['评测执行', 'Evaluation execution'], evaluation_update: ['评测更新', 'Evaluation update'],
  graph_query: ['图谱查询', 'Graph query'], graph_update: ['图谱更新', 'Graph update'], contract_collect: ['契约采集', 'Contract collection'],
  plugin_service: ['插件接口调用', 'Plugin service call'],
  run_start: ['任务开始', 'Run start'], round_before: ['每轮开始', 'Round before'],
  llm_before: ['模型调用前', 'Model before'], llm_after: ['模型调用后', 'Model after'],
  tool_batch_before: ['工具批次开始', 'Tool batch before'], tool_before: ['工具执行前', 'Tool before'],
  tool_after: ['工具执行后', 'Tool after'], tool_batch_after: ['工具批次结束', 'Tool batch after'],
  round_after: ['每轮结束', 'Round after'], run_finalize: ['任务收尾', 'Run finalize'],
  run_end: ['执行结束', 'Execution end'], error: ['异常通知', 'Error'], cancel: ['取消通知', 'Cancel'],
  'model-call': ['模型调用', 'Model invocation'], 'tool-call': ['工具执行', 'Tool execution'],
};

export const COLORS: Record<string, string> = {
  plugin: '#5265c8', stage: '#177875', interface: '#687588', execution: '#315170',
  observer: '#2b7ca4', transform: '#9a6b18', control: '#9455b6', service: '#25854e',
};

export function edgePath(edge: PlanEdge, source: PlanNode, target: PlanNode): string {
  if (edge.kind === 'flow') {
    return `M ${source.x + source.width / 2} ${source.y + source.height} L ${target.x + target.width / 2} ${target.y}`;
  }
  if (edge.kind === 'branch') {
    const lane = edge.label === 'continue' ? 6 : edge.label === 'no-tools' ? 16 : 26;
    return `M ${source.x} ${source.y + source.height / 2} H ${lane} V ${target.y + target.height / 2} H ${target.x}`;
  }
  if (edge.kind === 'order' && source.y !== target.y) {
    const middle = source.y + source.height + 6;
    return `M ${source.x + source.width / 2} ${source.y + source.height} V ${middle} H ${target.x + target.width / 2} V ${target.y}`;
  }
  const sx = source.x + source.width; const sy = source.y + source.height / 2;
  const tx = target.x; const ty = target.y + target.height / 2;
  return `M ${sx} ${sy} C ${sx + (tx - sx) / 2} ${sy}, ${sx + (tx - sx) / 2} ${ty}, ${tx} ${ty}`;
}

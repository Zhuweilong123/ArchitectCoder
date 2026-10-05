import { Alert, Button, Descriptions, Space, Tag } from 'antd';
import { stepExplanation, stepStatus, type ReplayStep } from './replayModel';

export const STATUS_COLORS: Record<string, string> = {
  executed: 'success', completed: 'success', skipped: 'default', error: 'error', failed: 'error',
  interrupted: 'warning', cancelled: 'warning', started: 'processing', recorded: 'blue',
  running: 'processing', blocked: 'warning', degraded: 'warning',
};

export function statusLabel(status: string, en: boolean): string {
  const labels: Record<string, [string, string]> = {
    executed: ['已执行', 'Executed'], skipped: ['已跳过', 'Skipped'], error: ['失败', 'Failed'],
    failed: ['失败', 'Failed'], interrupted: ['已中断', 'Interrupted'], cancelled: ['已取消', 'Cancelled'],
    started: ['调用开始', 'Call started'], completed: ['已完成', 'Completed'], recorded: ['阶段已记录', 'Stage recorded'],
    running: ['执行中', 'Running'], blocked: ['已阻断', 'Blocked'], degraded: ['已降级', 'Degraded'],
  };
  return labels[status]?.[en ? 1 : 0] || status;
}

export default function ReplayStepDetails({ steps, index, en, onLocate }: {
  steps: ReplayStep[]; index: number; en: boolean; onLocate: (index: number) => void;
}) {
  const event = steps[index].event;
  const tx = (zh: string, english: string) => en ? english : zh;
  const detail = stepExplanation(steps, index);
  const parentIndex = steps.findIndex((item) => item.event.event_type === 'operation' && item.event.operation_id === event.parent_operation_id);
  const status = stepStatus(event);
  const actions: Record<string, string> = {
    continue: tx('继续执行', 'Continue'), stop: tx('停止任务', 'Stop task'), veto: tx('阻止工具执行', 'Veto tool execution'),
    recover: tx('进入恢复流程', 'Recover'), finalize: tx('提前收尾', 'Finalize'), replace: tx('替换工具反馈', 'Replace tool feedback'),
  };
  const effects: Record<string, string> = {
    block: tx('按失败策略阻断后续执行', 'Block subsequent execution under the failure policy'),
    continue: tx('记录异常后继续执行', 'Record failure and continue'),
    interrupt: tx('中断任务执行', 'Interrupt the task'),
    finalize: tx('批次结束后进入任务收尾', 'Finalize the task after the batch'),
    propagate: tx('异常交由领域调用方处理', 'The domain caller handles this failure'),
  };
  const text = (value: unknown) => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);
  const items = [
    { key: 'status', label: tx('状态', 'Status'), children: <Tag color={STATUS_COLORS[status]}>{statusLabel(status, en)}</Tag> },
    ...(typeof event.duration_ms === 'number' ? [{ key: 'duration', label: tx('耗时', 'Duration'), children: `${event.duration_ms} ms` }] : []),
    ...(event.plugin ? [{ key: 'plugin', label: tx('所属插件', 'Plugin'), children: text(event.plugin) }] : []),
    ...(event.operation_kind ? [{ key: 'kind', label: tx('操作类型', 'Operation kind'), children: text(event.operation_kind) }] : []),
    ...(event.scope ? [{ key: 'scope', label: tx('执行范围', 'Execution scope'), children: text(event.scope) }] : []),
    ...(event.operation_id ? [{ key: 'operation', label: tx('操作标识', 'Operation ID'), children: text(event.operation_id) }] : []),
    ...(event.parent_operation_id ? [{ key: 'parent', label: tx('父操作', 'Parent operation'), children: <Space wrap><span>{text(event.parent_operation_id)}</span>{parentIndex >= 0 && <Button size="small" onClick={() => onLocate(parentIndex)}>{tx('定位父操作', 'Locate parent')}</Button>}</Space> }] : []),
    ...(event.binding_stage && event.binding_stage !== event.stage ? [{ key: 'binding', label: tx('接口默认阶段', 'Default interface phase'), children: text(event.binding_stage) }] : []),
    ...(event.contribution_id ? [{ key: 'interface', label: tx('贡献接口', 'Contribution'), children: text(event.contribution_id) }] : []),
    ...(event.stage ? [{ key: 'stage', label: tx('阶段', 'Stage'), children: text(event.stage) }] : []),
    ...(event.mode ? [{ key: 'mode', label: tx('接口类型', 'Mode'), children: ({ observer: tx('观察', 'Observer'), transform: tx('数据处理', 'Transform'), control: tx('控制', 'Control'), service: tx('按需接口执行', 'On-demand service') } as Record<string, string>)[text(event.mode)] || text(event.mode) }] : []),
    ...(event.tool_name ? [{ key: 'tool', label: tx('工具', 'Tool'), children: text(event.tool_name) }] : []),
    ...(typeof event.ts_ms === 'number' ? [{ key: 'time', label: tx('记录时间', 'Recorded at'), children: new Date(event.ts_ms).toLocaleString(en ? 'en-US' : 'zh-CN') }] : []),
  ];
  return <div className="plugin-replay-step-details">
    {event.stage === 'run_end' && <Alert type="info" showIcon message={tx('本次执行区间已结束', 'This execution interval has ended')}
      description={tx('审核是否通过及后台任务是否完成，请查看任务状态和关联操作。', 'Check business task state and related operations for approval and background completion.')} />}
    <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 3 }} items={items} />
    {event.blocked_by ? <Alert type="warning" showIcon message={tx('该接口因前面的控制结果而跳过', 'This contribution was skipped by a preceding decision')}
      description={<Space wrap><span>{tx('触发接口：', 'Origin: ')}{text(event.blocked_by)}</span>
        {detail.blockerIndex >= 0 ? <Button size="small" onClick={() => onLocate(detail.blockerIndex)}>{tx('查看触发步骤', 'View originating step')}</Button>
          : <span>{tx('记录中没有对应触发步骤', 'No corresponding originating step in this recording')}</span>}</Space>} /> : null}
    {!!detail.action && <Alert type={['stop', 'veto', 'finalize'].includes(text(detail.action)) ? 'warning' : 'info'} showIcon
      message={`${tx('控制动作：', 'Decision: ')}${actions[text(detail.action)] || text(detail.action)}`}
      description={<div>{detail.reason ? <div>{tx('原因：', 'Reason: ')}{text(detail.reason)}</div> : <div>{tx('未记录原因', 'No reason recorded')}</div>}
        {detail.message ? <div>{tx('说明：', 'Explanation: ')}{text(detail.message)}</div> : null}</div>} />}
    {(status === 'error' || status === 'failed' || status === 'interrupted') && <Alert type={status === 'interrupted' ? 'warning' : 'error'} showIcon
      message={detail.errorType ? text(detail.errorType) : statusLabel(status, en)}
      description={<div><div>{detail.errorMessage ? text(detail.errorMessage) : tx('该记录没有异常详情；旧版记录可能只有失败状态。', 'No error details were recorded; older records may contain only a failure status.')}</div>
        {detail.failureEffect ? <div>{effects[text(detail.failureEffect)] || text(detail.failureEffect)}</div> : null}</div>} />}
    <details className="plugin-replay-event"><summary>{tx('原始事件记录', 'Raw event record')}</summary>
      <pre>{JSON.stringify(event, null, 2)}</pre></details>
  </div>;
}

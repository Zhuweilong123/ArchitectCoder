import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Empty, Select, Space, Spin, Tag } from 'antd';
import { getTrace, listTraces, type TraceMeta } from '../../services/api';
import type { PluginExecutionPlan } from '../../types/plugins';
import { contributionSummary, replayRuns, replaySteps, stepStatus, type ReplayStep } from './replayModel';
import ReplayStepDetails, { statusLabel, STATUS_COLORS } from './ReplayStepDetails';

interface Props {
  plan: PluginExecutionPlan;
  en: boolean;
  onStep: (step: ReplayStep | null) => void;
}

export default function PluginReplay({ plan, en, onStep }: Props) {
  const tx = (zh: string, english: string) => en ? english : zh;
  const [sessions, setSessions] = useState<TraceMeta[]>([]);
  const [session, setSession] = useState('');
  const [events, setEvents] = useState<Record<string, unknown>[]>([]);
  const [runId, setRunId] = useState('');
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const runs = useMemo(() => replayRuns(events), [events]);
  const run = runs.find((item) => item.id === runId);
  const steps = useMemo(() => run ? replaySteps(run, plan) : [], [run, plan]);
  const step = steps[index];
  const summary = useMemo(() => contributionSummary(steps), [steps]);
  useEffect(() => { onStep(step || null); }, [step, onStep]);
  useEffect(() => {
    const controller = new AbortController();
    setListLoading(true); setError('');
    void listTraces(controller.signal).then((data) => {
      if (!controller.signal.aborted) setSessions(data.filter((item) => !item.trace_type || item.trace_type === 'chat'));
    }).catch(() => { if (!controller.signal.aborted) setError('list'); })
      .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    setLoading(true); setError(''); setEvents([]); setRunId(''); setIndex(0);
    void getTrace(session, 'chat', controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setEvents(data.events); setRunId(replayRuns(data.events)[0]?.id || '');
    }).catch(() => { if (!controller.signal.aborted) setError('trace'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [session, revision]);
  const label = (item: ReplayStep) => String(item.event.contribution_id || item.event.stage || item.event.event_type);
  return <section className="plugin-replay">
    <Space wrap>
      <strong>{tx('实际执行回放', 'Execution history')}</strong>
      <Select showSearch optionFilterProp="label" aria-label={tx('选择会话', 'Select session')} placeholder={tx('选择 DevAgent 会话', 'Select a DevAgent session')}
        value={session || undefined} style={{ width: 270 }} onChange={(value) => { setSession(value); setEvents([]); setIndex(0); }}
        options={sessions.map((item) => ({ value: item.session_id, label: `${item.date || ''} ${item.title || item.session_id}` }))} />
      <Select aria-label={tx('选择任务', 'Select task')} placeholder={tx('选择任务', 'Select task')} value={runId || undefined} style={{ width: 230 }}
        onChange={(value) => { setRunId(value); setIndex(0); }} options={runs.map((item, i) => ({ value: item.id,
          label: `${i + 1}. ${String(item.events.find((event) => event.agent_name)?.agent_name || 'DevAgent')} · ${item.id.slice(0, 12)}` }))} />
      <Button onClick={() => setRevision((value) => value + 1)} disabled={loading || listLoading}>{tx('刷新记录', 'Refresh records')}</Button>
      {(loading || listLoading) && <Spin size="small" />}
    </Space>
    <div className="plugin-plan-caption">{tx('查看已记录的历史，不会重新执行模型或工具。任务按 run_id 分开，步骤按记录顺序展示。', 'Recorded history only. Tasks are separated by run_id; steps follow recording order.')}</div>
    {error && <Alert type="error" showIcon message={tx('无法加载执行记录，请检查连接后刷新重试。', 'Could not load history. Check the connection and refresh.')} />}
    {!listLoading && !error && !sessions.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tx('暂无聊天 Trace 记录', 'No chat traces yet')} />}
    {session && !loading && !error && !runs.length && <Alert type="info" message={tx('该会话没有阶段执行记录，请使用开启 Trace 的新版后端运行一次任务。', 'This session has no lifecycle records. Run a task with tracing enabled on the updated backend.')} />}
    {step && !step.compatible && <Alert type="warning" showIcon message={tx('记录缺少计划版本，或与当前计划不一致。可查看步骤详情，架构图高亮已停用。', 'The recorded plan is missing or differs from the current plan. Details remain available; graph highlighting is disabled.')} />}
    {!!steps.length && <>
      <div className="plugin-replay-summary">
        <span>{tx('本任务插件调用：', 'Task contributions: ')}</span>
        {(['executed', 'skipped', 'error', 'interrupted'] as const).map((status) => <Tag color={STATUS_COLORS[status]} key={status}>{statusLabel(status, en)} {summary[status]}</Tag>)}
        <Tag>{tx('控制决定', 'Decisions')} {summary.decisions}</Tag>
        <span>{tx('插件累计耗时', 'Cumulative contribution duration')} {summary.durationMs.toFixed(2)} ms</span>
      </div>
      <Space className="plugin-replay-navigation" wrap>
        <Button disabled={index === 0} onClick={() => setIndex((value) => value - 1)}>{tx('上一步', 'Previous')}</Button>
        <span>{index + 1} / {steps.length}</span>
        <Button disabled={index >= steps.length - 1} onClick={() => setIndex((value) => value + 1)}>{tx('下一步', 'Next')}</Button>
        <Select showSearch optionFilterProp="label" aria-label={tx('选择执行步骤', 'Select execution step')} value={index} style={{ width: 340 }} onChange={setIndex}
          options={steps.map((item, i) => ({ value: i, label: `${i + 1}. ${label(item)} · ${statusLabel(stepStatus(item.event), en)}` }))} />
      </Space>
      {step && <ReplayStepDetails steps={steps} index={index} en={en} onLocate={setIndex} />}
    </>}
  </section>;
}

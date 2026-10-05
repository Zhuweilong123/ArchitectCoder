import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Alert, Button, Descriptions, Drawer, Empty, Select, Space, Spin, Tag, Tooltip, Typography } from 'antd';
import { DownloadOutlined, MinusOutlined, PlusOutlined, ReloadOutlined, ExpandOutlined } from '@ant-design/icons';
import { getPluginPlan } from '../../services/api';
import type { ContributionMode, PluginExecutionPlan } from '../../types/plugins';
import { useUiStore } from '../../stores/uiStore';
import { buildPluginGraph, wrapGraphLabel, type GraphView, type PlanNode, type PlanEdge } from './pluginGraph';
import './PluginArchitecture.css';
import PluginReplay from './PluginReplay';
import type { ReplayStep } from './replayModel';

const STAGE_LABELS: Record<string, [string, string]> = {
  run_start: ['任务开始', 'Run start'], round_before: ['每轮开始', 'Round before'],
  llm_before: ['模型调用前', 'Model before'], llm_after: ['模型调用后', 'Model after'],
  tool_batch_before: ['工具批次开始', 'Tool batch before'], tool_before: ['工具执行前', 'Tool before'],
  tool_after: ['工具执行后', 'Tool after'], tool_batch_after: ['工具批次结束', 'Tool batch after'],
  round_after: ['每轮结束', 'Round after'], run_finalize: ['任务收尾', 'Run finalize'],
  run_end: ['任务结束', 'Run end'], error: ['异常通知', 'Error'], cancel: ['取消通知', 'Cancel'],
  'model-call': ['模型调用', 'Model invocation'], 'tool-call': ['工具执行', 'Tool execution'],
};
const COLORS: Record<string, string> = { plugin: '#5265c8', stage: '#177875', interface: '#687588', execution: '#315170',
  observer: '#2b7ca4', transform: '#9a6b18', control: '#9455b6' };

function edgePath(edge: PlanEdge, source: PlanNode, target: PlanNode): string {
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

const PluginArchitecture: React.FC = () => {
  const language = useUiStore((s) => s.interfaceLanguage);
  const close = useUiStore((s) => s.setPluginArchitectureVisible);
  const en = language === 'en';
  const tx = (zh: string, english: string) => en ? english : zh;
  const stageLabel = (key: string) => STAGE_LABELS[key]?.[en ? 1 : 0] || key;
  const modeLabel = (mode: ContributionMode) => ({ observer: tx('观察', 'Observer'), transform: tx('数据处理', 'Transform'), control: tx('控制', 'Control') })[mode];
  const statusLabel = (status: string) => ({ discovered: tx('已发现', 'Discovered'), disabled: tx('已禁用', 'Disabled'), unavailable: tx('不可用', 'Unavailable') })[status] || status;
  const [plan, setPlan] = useState<PluginExecutionPlan | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [view, setView] = useState<GraphView>('organization');
  const [filter, setFilter] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [zoom, setZoom] = useState(1);
  const [replayVisible, setReplayVisible] = useState(false);
  const [replayStep, setReplayStep] = useState<ReplayStep | null>(null);
  const onReplayStep = useCallback((step: ReplayStep | null) => {
    setReplayStep(step); setSelectedId('');
    if (step) { setView('schedule'); setFilter(''); }
  }, []);
  const viewport = useRef<HTMLDivElement>(null);
  const markerId = useId().replace(/:/g, '');
  const graph = useMemo(() => plan ? buildPluginGraph(plan, view, filter) : null, [plan, view, filter]);
  const selected = graph?.nodes.find((node) => node.id === selectedId);
  const selectedPlugin = selected?.plugin || (selected?.contribution
    ? plan?.plugins.find((plugin) => plugin.name === selected.contribution?.plugin) : undefined);
  const counts = plan ? { plugins: plan.plugins.length, contributions: plan.stages.reduce((count, stage) => count + stage.contributions.length, 0),
    failed: plan.plugins.filter((plugin) => plugin.status === 'unavailable').length } : null;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    void getPluginPlan(controller.signal).then((data) => {
      if (!controller.signal.aborted) {
        setPlan(data); setSelectedId('');
        setFilter((value) => value === 'core' || data.plugins.some((plugin) => plugin.name === value) ? value : '');
      }
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      const status = axios.isAxiosError(reason) ? reason.response?.status : undefined;
      setError(status === 401 || status === 403 ? 'auth' : status === 404 ? 'missing' : status === 503 ? 'starting'
        : reason instanceof Error && reason.message === 'Unsupported plugin execution plan' ? 'unsupported' : 'network');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision]);

  const fit = useCallback(() => {
    if (graph && viewport.current) {
      setZoom(Math.max(0.25, Math.min(1, (viewport.current.clientWidth - 24) / graph.width)));
    }
  }, [graph]);
  useEffect(() => {
    fit();
    const element = viewport.current;
    if (!element) return;
    element.scrollTo(0, 0);
    const observer = new ResizeObserver(fit); observer.observe(element);
    return () => observer.disconnect();
  }, [fit]);

  useEffect(() => {
    if (!replayStep || !graph || !viewport.current) return;
    const node = graph.nodes.find((item) => item.id === replayStep.nodeIds[replayStep.nodeIds.length - 1]);
    if (node) viewport.current.scrollTo({ left: Math.max(0, node.x * zoom - 24), top: Math.max(0, node.y * zoom - 24), behavior: 'smooth' });
  }, [replayStep, graph, zoom]);

  const download = () => {
    if (!plan) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(plan, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'plugin-plan.json';
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const selectNode = (id: string) => {
    setSelectedId(id);
    const node = graph?.nodes.find((item) => item.id === id); const element = viewport.current;
    if (!node || !element) return;
    const x = node.x * zoom + 12; const y = node.y * zoom + 12;
    if (x < element.scrollLeft || x + node.width * zoom > element.scrollLeft + element.clientWidth
      || y < element.scrollTop || y + node.height * zoom > element.scrollTop + element.clientHeight) {
      element.scrollTo({ left: Math.max(0, x - 24), top: Math.max(0, y - 24), behavior: 'smooth' });
    }
  };
  const errorText: Record<string, string> = {
    auth: tx('访问被拒绝，请检查前端 API 认证配置。', 'Access denied. Check the frontend API authentication configuration.'),
    missing: tx('后端尚未提供插件架构接口，请更新并重启后端。', 'This backend does not expose plugin plans. Update and restart it.'),
    starting: tx('后端执行计划尚未初始化，请稍后重试。', 'The backend execution plan is not initialized. Retry shortly.'),
    network: tx('无法加载执行计划，请检查后端连接后重试。', 'Could not load the plan. Check the backend connection and retry.'),
    unsupported: tx('无法识别执行计划版本，请更新前后端。', 'Unsupported execution-plan version. Update the frontend and backend.'),
  };
  const related = (node: PlanNode) => replayStep?.nodeIds.length ? replayStep.nodeIds.includes(node.id) : !selected || node.id === selected.id || graph?.edges.some((edge) =>
    edge.source === selected.id && edge.target === node.id || edge.target === selected.id && edge.source === node.id);
  const subtitle = (node: PlanNode) => node.contribution
    ? `${node.contribution.order}. ${node.contribution.plugin} · ${modeLabel(node.contribution.mode)}`
    : node.kind === 'stage' ? node.label : node.kind === 'plugin' ? statusLabel(node.plugin!.status)
      : node.kind === 'interface' ? tx('领域接口 · 按需调用', 'Domain interface · on demand') : tx('核心执行节点', 'Core execution');
  const color = (node: PlanNode) => node.plugin?.status === 'unavailable' ? '#be4242'
    : node.plugin?.status === 'disabled' ? '#9299a6' : COLORS[node.contribution?.mode || node.kind];
  const branchLabels = { continue: tx('继续下一轮', 'Next round'), 'no-tools': tx('无工具调用', 'No tools'), finish: tx('返回最终结果', 'Finalize'),
    blocked: tx('调用被阻断', 'Blocked'), end: tx('进入任务结束', 'Run end') };

  return <Drawer title={tx('插件架构', 'Plugin architecture')} open onClose={() => close(false)} width="96vw"
    styles={{ body: { padding: 0, overflow: 'hidden' } }}>
    <div className="plugin-architecture">
      <div className="plugin-plan-header">
        <div><Typography.Text type="secondary">{tx('初始化执行计划', 'Initialized execution plan')}</Typography.Text>
          {counts && <span className="plugin-plan-counts">{tx(`${counts.plugins} 个插件 · ${counts.contributions} 个阶段接口`, `${counts.plugins} plugins · ${counts.contributions} contributions`)}</span>}
        </div>
        <Space wrap>
          <Button type={replayVisible ? 'primary' : 'default'} disabled={!plan} onClick={() => {
            setReplayVisible((value) => !value); setReplayStep(null); setSelectedId('');
          }}>{tx('执行回放', 'Execution history')}</Button>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => setRevision((value) => value + 1)}>{tx('刷新', 'Refresh')}</Button>
          <Button icon={<DownloadOutlined />} disabled={!plan} onClick={download}>{tx('导出计划', 'Export plan')}</Button>
        </Space>
        <div className="plugin-plan-caption">{tx('点击节点查看详情；打开执行回放，选择会话与任务，逐步查看实际执行节点。', 'Select nodes for details, or open execution history to inspect a recorded task step by step.')}</div>
        {plan && <Typography.Text className="plugin-plan-id" type="secondary" title={plan.plan_id}>Plan {plan.plan_id.slice(0, 16)}</Typography.Text>}
      </div>
      {error && <Alert type="error" showIcon message={errorText[error]} description={plan ? tx('当前保留上次加载的计划，可能已过期。', 'The previous plan remains visible and may be stale.') : undefined} />}
      {!!counts?.failed && <Alert type="warning" showIcon message={tx(`${counts.failed} 个插件不可用，可点击对应节点查看原因。`, `${counts.failed} plugins are unavailable. Select their nodes for details.`)} />}
      <div className="plugin-plan-tools">
        <Space wrap>
          <Select aria-label={tx('图形视图', 'Graph view')} value={view} style={{ width: 140 }} onChange={(value: GraphView) => { setView(value); setSelectedId(''); }}
            options={[{ value: 'organization', label: tx('组织图', 'Organization') }, { value: 'schedule', label: tx('调度图', 'Schedule') }]} />
          <Select showSearch optionFilterProp="label" aria-label={tx('筛选插件', 'Filter plugins')} value={filter} style={{ width: 220 }}
            onChange={(value) => { setFilter(value); setSelectedId(''); }} options={[
              { value: '', label: tx('全部插件', 'All plugins') }, { value: 'core', label: tx('core · 核心策略', 'core · Core policy') },
              ...(plan?.plugins.map((plugin) => ({ value: plugin.name, label: `${plugin.name} · ${statusLabel(plugin.status)}` })) || []),
            ]} />
          <Tooltip title={tx('缩小', 'Zoom out')}><Button aria-label={tx('缩小', 'Zoom out')} icon={<MinusOutlined />} onClick={() => setZoom((value) => Math.max(0.25, value - 0.1))} /></Tooltip>
          <span>{Math.round(zoom * 100)}%</span>
          <Tooltip title={tx('放大', 'Zoom in')}><Button aria-label={tx('放大', 'Zoom in')} icon={<PlusOutlined />} onClick={() => setZoom((value) => Math.min(2, value + 0.1))} /></Tooltip>
          <Button icon={<ExpandOutlined />} onClick={fit}>{tx('适应宽度', 'Fit width')}</Button>
        </Space>
        <div className="plugin-plan-legend">{(['observer', 'transform', 'control'] as const).map((mode) =>
          <Tag color={COLORS[mode]} key={mode}>{modeLabel(mode)}</Tag>)}<span>{tx('虚线：接口挂接／顺序', 'Dashed: bindings / order')}</span></div>
      </div>
      <div className="plugin-plan-main">
        <div className="plugin-plan-viewport" ref={viewport} aria-busy={loading}>
          {loading && <div className="plugin-plan-loading"><Spin /><span>{tx('正在加载计划…', 'Loading plan…')}</span></div>}
          {!graph && !loading && <Empty description={tx('尚未加载执行计划', 'No execution plan loaded')} />}
          {graph && <svg className="plugin-plan-svg" width={graph.width * zoom} height={graph.height * zoom} viewBox={`0 0 ${graph.width} ${graph.height}`}
            aria-label={view === 'organization' ? tx('插件组织图', 'Plugin organization graph') : tx('生命周期调度图', 'Lifecycle schedule graph')}>
            <defs><marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8190a7" /></marker></defs>
            {view === 'organization' && [tx('插件', 'Plugins'), tx('贡献 / 领域接口', 'Contributions / interfaces'), tx('生命周期阶段', 'Lifecycle stages')].map((label, index) =>
              <text key={index} x={[24, 316, 750][index]} y={30} className="plugin-plan-column">{label}</text>)}
            {graph.edges.map((edge, index) => {
              const source = graph.nodes.find((node) => node.id === edge.source); const target = graph.nodes.find((node) => node.id === edge.target);
              if (!source || !target) return null;
              const highlighted = replayStep?.nodeIds.length ? replayStep.nodeIds.includes(source.id) && replayStep.nodeIds.includes(target.id)
                : selected && (source.id === selected.id || target.id === selected.id);
              const lane = edge.label === 'continue' ? 6 : edge.label === 'no-tools' ? 16 : 26;
              const labelY = (source.y + target.y + source.height / 2 + target.height / 2) / 2;
              return <g key={index}><path d={edgePath(edge, source, target)} fill="none" stroke={highlighted ? '#1677ff' : '#8190a7'}
                strokeWidth={highlighted ? 2 : 1.2} opacity={selected ? highlighted ? 1 : 0.15 : edge.kind === 'binding' ? 0.4 : 0.8}
                strokeDasharray={edge.kind === 'binding' || edge.kind === 'order' ? '5 4' : undefined} markerEnd={`url(#${markerId})`}>
                <title>{edge.label ? branchLabels[edge.label] : `${source.label} → ${target.label}`}</title>
              </path>{edge.label && <text x={lane - 3} y={labelY} textAnchor="middle" transform={`rotate(-90 ${lane - 3} ${labelY})`}
                className="plugin-plan-branch-label" opacity={selected && !highlighted ? 0.25 : 1}>{branchLabels[edge.label]}</text>}</g>;
            })}
            {graph.nodes.map((node) => {
              const label = node.kind === 'stage' || node.kind === 'execution' ? stageLabel(node.label) : node.label;
              const lines = wrapGraphLabel(label, node.width > 260 ? 38 : 28);
              return <g key={node.id} transform={`translate(${node.x},${node.y})`} role="button" tabIndex={0}
                aria-label={`${label} · ${subtitle(node)}`} aria-pressed={selectedId === node.id}
                onClick={() => selectNode(node.id)} onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(node.id); }
                }} className="plugin-plan-node" opacity={related(node) ? 1 : 0.35}>
                <title>{label}</title>
                <rect width={node.width} height={node.height} rx={9} fill={replayStep?.nodeIds.includes(node.id) ? '#e6f4ff' : '#fff'} stroke={replayStep?.nodeIds.includes(node.id) || selectedId === node.id ? '#1677ff' : color(node)} strokeWidth={replayStep?.nodeIds.includes(node.id) || selectedId === node.id ? 2.5 : 1.2} />
                <rect width={5} height={node.height - 14} y={7} rx={2} fill={color(node)} />
                <text x={14} y={22} className="plugin-plan-node-title">{lines.map((line, index) => <tspan x={14} dy={index ? 16 : 0} key={index}>{line}</tspan>)}</text>
                <text x={14} y={node.height - 9} className="plugin-plan-node-subtitle">{wrapGraphLabel(subtitle(node), node.width > 260 ? 48 : 34)[0]}</text>
              </g>;
            })}
          </svg>}
        </div>
        <aside className="plugin-plan-details">
          <div className="plugin-plan-detail-head"><strong>{tx('节点详情', 'Node details')}</strong>
            {selected && <Button size="small" type="text" onClick={() => setSelectedId('')}>{tx('清除选择', 'Clear')}</Button>}</div>
          {!selected ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tx('点击图中节点，查看其接口与组织关系', 'Select a graph node to inspect its interfaces and relationships')} /> : <>
            <Typography.Title level={5}>{selected.kind === 'stage' || selected.kind === 'execution' ? stageLabel(selected.label) : selected.label}</Typography.Title>
            {selectedPlugin && <Descriptions column={1} size="small" bordered items={[
              { key: 'status', label: tx('状态', 'Status'), children: statusLabel(selectedPlugin.status) },
              { key: 'provider', label: 'Provider', children: selectedPlugin.provider || tx('核心内置', 'Built in') },
              { key: 'source', label: tx('来源', 'Source'), children: selectedPlugin.source },
            ]} />}
            {selectedPlugin?.error && <Alert type="error" showIcon message={tx('加载失败原因', 'Load failure')} description={selectedPlugin.error} />}
            {selected.contribution && <Descriptions column={1} size="small" bordered items={[
              { key: 'plugin', label: tx('插件', 'Plugin'), children: selected.contribution.plugin },
              { key: 'stage', label: tx('阶段', 'Stage'), children: `${stageLabel(selected.contribution.stage)} (${selected.contribution.stage})` },
              { key: 'mode', label: tx('类型', 'Mode'), children: modeLabel(selected.contribution.mode) },
              { key: 'order', label: tx('阶段内顺序', 'Stage order'), children: selected.contribution.order },
              { key: 'priority', label: tx('声明优先级', 'Priority'), children: selected.contribution.priority },
              { key: 'handler', label: tx('接口', 'Handler'), children: selected.contribution.handler },
              { key: 'before', label: tx('先于', 'Before'), children: selected.contribution.before.join(', ') || '—' },
              { key: 'after', label: tx('后于', 'After'), children: selected.contribution.after.join(', ') || '—' },
              { key: 'scope', label: tx('作用域', 'Scope'), children: selected.contribution.scope },
              { key: 'failure', label: tx('接口异常时', 'On failure'), children: selected.contribution.fail_closed ? tx('阻断执行', 'Block execution') : tx('记录并继续', 'Record and continue') },
            ]} />}
            {selected.stage && <>
              <p>{tx('允许的外部接口类型', 'Supported external modes')}</p>
              {selected.stage.supported_modes.map((mode) => <Tag key={mode}>{modeLabel(mode)}</Tag>)}
              <p>{tx('本阶段接口（按执行顺序）', 'Stage contributions in execution order')}</p>
              <div className="plugin-plan-detail-links">{selected.stage.contributions.slice().sort((a, b) => a.order - b.order).map((item) =>
                <Button key={item.id} size="small" type="link" disabled={!graph?.nodes.some((node) => node.id === `contribution:${item.id}`)} onClick={() => selectNode(`contribution:${item.id}`)}>{item.order}. {item.id}</Button>)}
                {!selected.stage.contributions.length && <Typography.Text type="secondary">{tx('没有贡献接口', 'No contributions')}</Typography.Text>}</div>
            </>}
            {selected.kind === 'plugin' && <p>{tx('按需领域接口与阶段贡献分开组织。发现成功不代表 provider 已实例化或通过健康检查。', 'Domain interfaces are separate from lifecycle contributions. Discovery does not imply provider instantiation or health checks.')}</p>}
            {selected.kind === 'interface' && <p>{tx('该接口由领域调用方按需使用，未自动挂接到生命周期阶段。', 'This interface is called by its domain consumer and is not automatically attached to a lifecycle stage.')}</p>}
            {selected.kind === 'execution' && <p>{tx('由核心执行器调用；前后阶段的接口可观察或参与处理。', 'The core executor performs this operation; surrounding phases expose observation and processing hooks.')}</p>}
          </>}
        </aside>
      </div>
      {replayVisible && plan && <PluginReplay plan={plan} en={en} onStep={onReplayStep} />}
    </div>
  </Drawer>;
};

export default PluginArchitecture;

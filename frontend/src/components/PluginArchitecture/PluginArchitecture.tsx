import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Alert, AutoComplete, Button, Descriptions, Drawer, Empty, Select, Space, Spin, Tag, Tooltip, Typography } from 'antd';
import { DownloadOutlined, MinusOutlined, PlusOutlined, ReloadOutlined, ExpandOutlined } from '@ant-design/icons';
import { getPluginPlan, getPluginDiagnostics, refreshPlugins } from '../../services/api';
import type { ContributionMode, PluginExecutionPlan, PluginLoadReport, PluginRefreshResult } from '../../types/plugins';
import { useUiStore } from '../../stores/uiStore';
import { buildPluginGraph, wrapGraphLabel, type GraphView, type PlanNode, type PlanEdge } from './pluginGraph';
import { STAGE_LABELS, COLORS, edgePath } from './graphPresentation';
import { createPluginArchitectureHtml } from './pluginArchitectureHtml';
import './PluginArchitecture.css';
import PluginReplay from './PluginReplay';
import { stepStatus, type ReplayStep } from './replayModel';
import { graphCatalog, graphIssues, revealGraphNode, searchGraph, type GraphIssue } from './graphExplorer';

const PluginArchitecture: React.FC = () => {
  const language = useUiStore((s) => s.interfaceLanguage);
  const close = useUiStore((s) => s.setPluginArchitectureVisible);
  const en = language === 'en';
  const tx = (zh: string, english: string) => en ? english : zh;
  const stageLabel = (key: string) => STAGE_LABELS[key]?.[en ? 1 : 0] || key;
  const modeLabel = (mode: ContributionMode) => ({ observer: tx('观察', 'Observer'), transform: tx('数据处理', 'Transform'), control: tx('控制', 'Control'), service: tx('按需接口执行', 'On-demand service') })[mode];
  const statusLabel = (status: string) => ({ discovered: tx('已发现', 'Discovered'), disabled: tx('已禁用', 'Disabled'), unavailable: tx('不可用', 'Unavailable') })[status] || status;
  const [plan, setPlan] = useState<PluginExecutionPlan | null>(null);
  const [loadReport, setLoadReport] = useState<PluginLoadReport | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshResult, setRefreshResult] = useState<PluginRefreshResult | null>(null);
  const [refreshError, setRefreshError] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [view, setView] = useState<GraphView>('schedule');
  const [expandedPlugins, setExpandedPlugins] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [zoom, setZoom] = useState(1);
  const [replayVisible, setReplayVisible] = useState(false);
  const [replayStep, setReplayStep] = useState<ReplayStep | null>(null);
  const [replaySteps, setReplaySteps] = useState<ReplayStep[]>([]);
  const [replayLocate, setReplayLocate] = useState<{ index: number; token: number } | null>(null);
  const replayStatus = replayStep ? stepStatus(replayStep.event) : '';
  const replayColor = ({ executed: '#389e0d', completed: '#389e0d', error: '#cf1322', failed: '#cf1322',
    skipped: '#ad6800', interrupted: '#d46b08', cancelled: '#d46b08' } as Record<string, string>)[replayStatus] || '#1677ff';
  const onReplayStep = useCallback((step: ReplayStep | null) => {
    setReplayStep(step); setSelectedId('');
    if (step) { setView('schedule'); setFilter(''); }
    if (step?.compatible) {
      const name = String(step.event.plugin || plan?.stages.flatMap((stage) => stage.contributions)
        .find((item) => step.nodeIds.includes(`contribution:${item.id}`))?.plugin || '');
      if (name) setExpandedPlugins((values) => values.includes(name) ? values : [...values, name]);
    }
  }, [plan]);
  const onReplaySteps = useCallback((steps: ReplayStep[]) => { setReplaySteps(steps); setReplayLocate(null); }, []);
  const viewport = useRef<HTMLDivElement>(null);
  const notifications = useRef<HTMLDetailsElement>(null);
  const markerId = useId().replace(/:/g, '');
  const graph = useMemo(() => plan ? buildPluginGraph(plan, view, filter, { expandedPlugins }) : null, [plan, view, filter, expandedPlugins]);
  const catalog = useMemo(() => plan ? graphCatalog(plan) : [], [plan]);
  const matches = useMemo(() => searchGraph(catalog, query), [catalog, query]);
  const issues = useMemo(() => plan ? graphIssues(plan, loadReport, replaySteps) : [], [plan, loadReport, replaySteps]);
  const selected = graph?.nodes.find((node) => node.id === selectedId) || catalog.find((node) => node.id === selectedId);
  const selectedPlugin = selected?.plugin || (selected?.contribution
    ? catalog.find((node) => node.id === `plugin:${selected.contribution?.plugin}`)?.plugin : undefined);
  const selectedLoad = loadReport?.plan_id === plan?.plan_id
    ? loadReport?.plugins.find((plugin) => plugin.name === selectedPlugin?.name) : undefined;
  const selectedDiagnostics = [...(selectedPlugin?.diagnostics || []), ...(selectedLoad?.diagnostics || [])];
  const counts = plan ? { plugins: plan.plugins.length, contributions: [...plan.stages, ...(plan.notifications || [])].reduce((count, stage) => count + stage.contributions.length, 0),
    failed: plan.plugins.filter((plugin) => plugin.status === 'unavailable').length } : null;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    void Promise.all([getPluginPlan(controller.signal), getPluginDiagnostics(controller.signal)]).then(([data, report]) => {
      if (!controller.signal.aborted) {
        setPlan(data); setLoadReport(report); setSelectedId('');
        setRefreshResult(report.last_refresh || null);
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
    if (!graph || !viewport.current) return;
    const id = replayStep?.nodeIds[replayStep.nodeIds.length - 1] || selectedId;
    const node = graph.nodes.find((item) => item.id === id)
      || graph.nodes.find((item) => item.id === `plugin:${selectedPlugin?.name}`)
      || graph.nodes.find((item) => selectedPlugin && (item.plugin?.name === selectedPlugin.name || item.contribution?.plugin === selectedPlugin.name));
    if (node) viewport.current.scrollTo({ left: Math.max(0, (node.x + node.width / 2) * zoom - viewport.current.clientWidth / 2),
      top: Math.max(0, (node.y + node.height / 2) * zoom - viewport.current.clientHeight / 2), behavior: 'smooth' });
  }, [replayStep, selectedId, selectedPlugin?.name, graph, zoom]);
  useEffect(() => {
    const stage = selected?.contribution?.stage || selected?.stage?.stage;
    if (stage && plan && !plan.stages.some((item) => item.stage === stage) && notifications.current) notifications.current.open = true;
  }, [selected, plan]);

  const download = (format: 'json' | 'html' = 'json') => {
    if (!plan) return;
    const html = format === 'html';
    const content = html ? createPluginArchitectureHtml(plan, { language, view, filter, loadReport, expandedPlugins, query }) : JSON.stringify(plan, null, 2);
    const url = URL.createObjectURL(new Blob([content], { type: html ? 'text/html;charset=utf-8' : 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = html ? 'plugin-architecture.html' : 'plugin-plan.json';
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const discoverPlugins = async () => {
    setRefreshing(true); setRefreshError(false); setRefreshResult(null);
    try {
      const result = await refreshPlugins();
      setRefreshResult(result);
      if (result.status === 'published') {
        setReplayStep(null); setReplaySteps([]); setReplayVisible(false);
        setRevision((value) => value + 1);
      }
    } catch { setRefreshError(true); }
    finally { setRefreshing(false); }
  };
  const selectNode = (id: string) => {
    const node = graph?.nodes.find((item) => item.id === id) || catalog.find((item) => item.id === id);
    if (!node) return;
    const name = node.plugin?.name || node.contribution?.plugin;
    setReplayStep(null);
    if (node.summary && id.startsWith('group:')) { openPlugin(name!); return; }
    if (!plan) return;
    const next = revealGraphNode(plan, catalog, id, { view, filter, expandedPlugins });
    setExpandedPlugins(next.expandedPlugins); setView(next.view); setFilter(next.filter);
    setSelectedId(id);
  };
  const openPlugin = (name: string) => {
    setReplayStep(null); setView('organization'); setFilter(name); setSelectedId(`plugin:${name}`);
    setExpandedPlugins((values) => values.includes(name) ? values : [...values, name]);
  };
  const togglePlugin = (name: string) => {
    setReplayStep(null); setSelectedId(`plugin:${name}`);
    setExpandedPlugins((values) => values.includes(name) ? values.filter((value) => value !== name) : [...values, name]);
  };
  const overview = () => { setView('schedule'); setFilter(''); setSelectedId(''); setReplayStep(null); setExpandedPlugins([]); setQuery(''); };
  const issueLabel = (issue: GraphIssue) => `${({ declaration: tx('声明失败', 'Declaration'), load: tx('加载失败', 'Load'), execution: tx('执行失败', 'Execution') })[issue.source]}${issue.compatible === false ? tx('（历史计划）', ' (historical plan)') : ''} · ${issue.plugin} · ${issue.code} · ${issue.message}`;
  const locateIssue = (id: string) => {
    const issue = issues.find((item) => item.id === id);
    if (!issue) return;
    if (issue.source === 'execution') {
      setReplayLocate({ index: issue.stepIndex!, token: Date.now() });
      if (!issue.compatible) return;
    }
    selectNode(issue.nodeId);
  };
  const nodeIssues = (node: PlanNode) => issues.filter((issue) => issue.compatible !== false
    && (node.plugin ? issue.plugin === node.plugin.name : issue.nodeId === node.id));
  const errorText: Record<string, string> = {
    auth: tx('访问被拒绝，请检查前端 API 认证配置。', 'Access denied. Check the frontend API authentication configuration.'),
    missing: tx('后端尚未提供插件架构接口，请更新并重启后端。', 'This backend does not expose plugin plans. Update and restart it.'),
    starting: tx('后端执行计划尚未初始化，请稍后重试。', 'The backend execution plan is not initialized. Retry shortly.'),
    network: tx('无法加载执行计划，请检查后端连接后重试。', 'Could not load the plan. Check the backend connection and retry.'),
    unsupported: tx('无法识别执行计划版本，请更新前后端。', 'Unsupported execution-plan version. Update the frontend and backend.'),
  };
  const related = (node: PlanNode) => replayStep?.nodeIds.length ? replayStep.nodeIds.includes(node.id) : !selected || node.id === selected.id
    || node.plugin?.name === selectedPlugin?.name && !!selectedPlugin || graph?.edges.some((edge) =>
    edge.source === selected.id && edge.target === node.id || edge.target === selected.id && edge.source === node.id);
  const subtitle = (node: PlanNode) => node.contribution
    ? `${node.contribution.order}. ${node.contribution.plugin} · ${modeLabel(node.contribution.mode)}`
    : node.kind === 'stage' ? node.label : node.kind === 'plugin' ? `${statusLabel(node.plugin!.status)} · ${node.summary?.contributions || 0} ${tx('个贡献', 'contributions')}`
      : node.kind === 'interface' ? tx('领域接口 · 未安装绑定', 'Domain interface · binding not installed') : tx('核心执行节点', 'Core execution');
  const color = (node: PlanNode) => nodeIssues(node).length || node.plugin?.status === 'unavailable' ? '#be4242'
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
            setReplayVisible((value) => !value); setReplayStep(null); setReplaySteps([]); setSelectedId('');
          }}>{tx('执行回放', 'Execution history')}</Button>
          <Button icon={<ReloadOutlined />} loading={loading} disabled={refreshing} onClick={() => setRevision((value) => value + 1)}>{tx('刷新视图', 'Reload view')}</Button>
          <Tooltip title={tx('扫描新增插件并校验发布；运行中的任务继续使用原计划。已有插件或路由变化需要重启。', 'Validate and publish newly discovered plugins. Running tasks retain their plan. Existing plugin or route changes require restart.')}>
            <Button loading={refreshing} disabled={!plan || loading} onClick={discoverPlugins}>{tx('扫描新插件', 'Discover plugins')}</Button>
          </Tooltip>
          <Button icon={<DownloadOutlined />} disabled={!plan} onClick={() => download()}>{tx('导出计划', 'Export plan')}</Button>
          <Button icon={<DownloadOutlined />} disabled={!plan} onClick={() => download('html')}>{tx('导出 HTML', 'Export HTML')}</Button>
        </Space>
        <div className="plugin-plan-caption">{tx('从主流程总览进入插件详情；搜索或定位异常会自动展开目标。执行回放展示实际经过的阶段与接口。', 'Explore the main flow, then drill into plugins. Search and issues reveal their targets. Execution history shows recorded stages and interfaces.')}</div>
        {plan && <Typography.Text className="plugin-plan-id" type="secondary" title={plan.plan_id}>Plan {plan.plan_id.slice(0, 16)}</Typography.Text>}
      </div>
      {error && <Alert type="error" showIcon message={errorText[error]} description={plan ? tx('当前保留上次加载的计划，可能已过期。', 'The previous plan remains visible and may be stale.') : undefined} />}
      {refreshError && <Alert type="error" showIcon message={tx('无法获取扫描结果，请检查连接并刷新视图以确认当前计划。', 'Could not retrieve the discovery result. Check the connection and reload the view to confirm the active plan.')} />}
      {refreshResult && <Alert showIcon type={refreshResult.status === 'rejected' ? 'warning' : refreshResult.status === 'published' ? 'success' : 'info'}
        message={refreshResult.status === 'published' ? tx(`新计划已发布，新增插件：${refreshResult.added.join(', ')}`, `New plan published. Added: ${refreshResult.added.join(', ')}`)
          : refreshResult.status === 'unchanged' ? tx('没有新增可发布的插件，当前计划保持不变。', 'No newly publishable plugins. The active plan is unchanged.')
            : tx('候选计划未发布，当前计划继续运行。', 'Candidate plan was rejected; the active plan continues to run.')}
        description={refreshResult.status === 'rejected' ? <ul>{refreshResult.diagnostics.map((item, index) => <li key={index}>
          {item.plugin && <strong>{item.plugin}: </strong>}{item.code === 'restart_required' ? tx('该变更需要重启后端。', 'This change requires a backend restart.') : item.code} · {item.message}
        </li>)}</ul> : refreshResult.status === 'published' ? tx('新任务使用新计划，运行中的任务继续使用原计划。', 'New tasks use the new plan; running tasks retain their original plan.') : undefined} />}
      {!!counts?.failed && <Alert type="warning" showIcon message={tx(`${counts.failed} 个插件不可用，可点击对应节点查看原因。`, `${counts.failed} plugins are unavailable. Select their nodes for details.`)} />}
      <div className="plugin-plan-tools">
        <Space wrap>
          <Button onClick={overview}>{tx('主流程总览', 'Main flow')}</Button>
          <Select aria-label={tx('图形视图', 'Graph view')} value={view} style={{ width: 140 }} onChange={(value: GraphView) => { setView(value); setReplayStep(null); }}
            options={[{ value: 'organization', label: tx('组织图', 'Organization') }, { value: 'schedule', label: tx('调度图', 'Schedule') }]} />
          <Select showSearch optionFilterProp="label" aria-label={tx('筛选插件', 'Filter plugins')} value={filter} style={{ width: 220 }}
            onChange={(value) => { setFilter(value); setSelectedId(''); setReplayStep(null); }} options={[
              { value: '', label: tx('全部插件', 'All plugins') }, { value: 'core', label: tx('core · 核心策略', 'core · Core policy') },
              ...(plan?.plugins.map((plugin) => ({ value: plugin.name, label: `${plugin.name} · ${statusLabel(plugin.status)}` })) || []),
            ]} />
          <AutoComplete value={query} onChange={setQuery} style={{ width: 260 }} aria-label={tx('搜索接口', 'Search interfaces')}
            placeholder={tx('搜索插件、接口或贡献 ID', 'Search plugin, interface or contribution ID')}
            options={matches.map((node) => ({ value: node.id, label: `${node.contribution?.plugin || node.plugin?.name || node.kind} · ${node.label}` }))}
            onSelect={(id) => { const node = catalog.find((item) => item.id === id); setQuery(node?.label || id); selectNode(id); }}
            notFoundContent={query.trim() ? tx('没有匹配的节点', 'No matching nodes') : null} allowClear />
          <Select value={undefined} showSearch optionFilterProp="label" style={{ width: 240 }} aria-label={tx('定位异常', 'Locate issue')}
            placeholder={tx(`定位异常（${issues.length}）`, `Locate issue (${issues.length})`)} disabled={!issues.length}
            options={issues.map((issue) => ({ value: issue.id, label: issueLabel(issue) }))} onChange={locateIssue} />
          <Button onClick={() => { setReplayStep(null); setExpandedPlugins(['core', ...(plan?.plugins.map((plugin) => plugin.name) || [])]); }}>{tx('全部展开', 'Expand all')}</Button>
          <Button onClick={() => { setReplayStep(null); setExpandedPlugins([]); setSelectedId(''); }}>{tx('全部收起', 'Collapse all')}</Button>
          <Tooltip title={tx('缩小', 'Zoom out')}><Button aria-label={tx('缩小', 'Zoom out')} icon={<MinusOutlined />} onClick={() => setZoom((value) => Math.max(0.25, value - 0.1))} /></Tooltip>
          <span>{Math.round(zoom * 100)}%</span>
          <Tooltip title={tx('放大', 'Zoom in')}><Button aria-label={tx('放大', 'Zoom in')} icon={<PlusOutlined />} onClick={() => setZoom((value) => Math.min(2, value + 0.1))} /></Tooltip>
          <Button icon={<ExpandOutlined />} onClick={fit}>{tx('适应宽度', 'Fit width')}</Button>
        </Space>
        <div className="plugin-plan-legend">{(['observer', 'transform', 'control', 'service'] as const).map((mode) =>
          <Tag color={COLORS[mode]} key={mode}>{modeLabel(mode)}</Tag>)}<span>{tx('虚线：接口挂接／顺序', 'Dashed: bindings / order')}</span></div>
      </div>
      {plan && <div className="plugin-plan-roster">
        <span>{filter ? tx('插件详情', 'Plugin detail') : tx('插件概况', 'Plugins')}</span>
        {['core', ...plan.plugins.map((plugin) => plugin.name)].map((name) => {
          const entry = catalog.find((node) => node.id === `plugin:${name}`)!;
          const count = [...plan.stages, ...(plan.notifications || [])].flatMap((stage) => stage.contributions).filter((item) => item.plugin === name).length;
          const failures = issues.filter((issue) => issue.plugin === name && issue.compatible !== false).length;
          return <Button key={name} size="small" type={filter === name ? 'primary' : 'default'} onClick={() => openPlugin(name)}
            title={`${name} · ${statusLabel(entry.plugin!.status)}`}>{name} · {count}{failures > 0 && <span className="plugin-plan-issue-count"> !{failures}</span>}</Button>;
        })}
      </div>}
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
              const lines = wrapGraphLabel(label, node.width > 260 ? 38 : 24);
              const failures = nodeIssues(node).length;
              return <g key={node.id} transform={`translate(${node.x},${node.y})`} role="button" tabIndex={0}
                aria-label={`${label} · ${subtitle(node)}`} aria-pressed={selectedId === node.id}
                onClick={() => selectNode(node.id)} onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(node.id); }
                }} className="plugin-plan-node" opacity={related(node) ? 1 : 0.35}>
                <title>{label}</title>
                <rect width={node.width} height={node.height} rx={9} fill={replayStep?.nodeIds.includes(node.id) ? '#f0f6ff' : '#fff'} stroke={replayStep?.nodeIds.includes(node.id) ? replayColor : selectedId === node.id ? '#1677ff' : color(node)} strokeWidth={replayStep?.nodeIds.includes(node.id) || selectedId === node.id ? 2.5 : 1.2} />
                <rect width={5} height={node.height - 14} y={7} rx={2} fill={color(node)} />
                <text x={14} y={22} className="plugin-plan-node-title">{lines.map((line, index) => <tspan x={14} dy={index ? 16 : 0} key={index}>{line}</tspan>)}</text>
                <text x={14} y={node.height - 9} className="plugin-plan-node-subtitle">{wrapGraphLabel(subtitle(node), node.width > 260 ? 48 : 34)[0]}</text>
                {node.kind === 'plugin' && node.plugin && <g role="button" tabIndex={0} aria-label={`${expandedPlugins.includes(node.plugin.name) ? tx('收起', 'Collapse') : tx('展开', 'Expand')} ${node.plugin.name}`}
                  onClick={(event) => { event.stopPropagation(); togglePlugin(node.plugin!.name); }}
                  onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); togglePlugin(node.plugin!.name); } }}>
                  <rect x={node.width - 30} y={8} width={22} height={22} rx={4} fill="#edf4ff" />
                  <text x={node.width - 19} y={24} textAnchor="middle" fill="#176bcc">{expandedPlugins.includes(node.plugin.name) ? '−' : '+'}</text>
                </g>}
                {failures > 0 && <text x={node.width - 9} y={node.height - 9} textAnchor="end" className="plugin-plan-node-issue">!{failures}</text>}
              </g>;
            })}
          </svg>}
        </div>
        <aside className="plugin-plan-details">
          <div className="plugin-plan-detail-head"><strong>{tx('节点详情', 'Node details')}</strong>
            {selected && <Button size="small" type="text" onClick={() => setSelectedId('')}>{tx('清除选择', 'Clear')}</Button>}</div>
          {!selected ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tx('点击图中节点，查看其接口与组织关系', 'Select a graph node to inspect its interfaces and relationships')} /> : <>
            <Typography.Title level={5}>{selected.kind === 'stage' || selected.kind === 'execution' ? stageLabel(selected.label) : selected.label}</Typography.Title>
            {selectedPlugin && <Space wrap>
              <Button size="small" onClick={() => togglePlugin(selectedPlugin.name)}>{expandedPlugins.includes(selectedPlugin.name) ? tx('收起插件', 'Collapse plugin') : tx('展开插件', 'Expand plugin')}</Button>
              <Button size="small" onClick={() => openPlugin(selectedPlugin.name)}>{tx('仅看此插件', 'Focus plugin')}</Button>
            </Space>}
            {selectedPlugin && <Descriptions column={1} size="small" bordered items={[
              { key: 'status', label: tx('状态', 'Status'), children: statusLabel(selectedPlugin.status) },
              { key: 'provider', label: 'Provider', children: selectedPlugin.provider || tx('核心内置', 'Built in') },
              { key: 'source', label: tx('来源', 'Source'), children: selectedPlugin.source },
              ...(selectedPlugin.version ? [{ key: 'version', label: tx('版本', 'Version'), children: selectedPlugin.version }] : []),
              ...(selectedPlugin.revision ? [{ key: 'revision', label: tx('内容指纹', 'Content fingerprint'), children: selectedPlugin.revision }] : []),
              ...(selectedLoad ? [{ key: 'runtime', label: tx('最近实例加载', 'Latest instance load'), children: ({ not_loaded: tx('尚未实例化', 'Not instantiated'), loaded: tx('加载成功', 'Loaded'), disabled: tx('已禁用', 'Disabled'), unavailable: tx('加载失败', 'Load failed') } as Record<string, string>)[selectedLoad.status] || selectedLoad.status }] : []),
              ...(selectedPlugin.slot ? [{ key: 'slot', label: tx('能力槽位', 'Capability slot'), children: selectedPlugin.slot }] : []),
              ...(selectedPlugin.dependencies?.length ? [{ key: 'dependencies', label: tx('必需插件', 'Required plugins'), children: selectedPlugin.dependencies.join(', ') }] : []),
              ...(selectedPlugin.optional_dependencies?.length ? [{ key: 'optional-dependencies', label: tx('可选插件', 'Optional plugins'), children: selectedPlugin.optional_dependencies.join(', ') }] : []),
              ...(selectedPlugin.config_keys?.length ? [{ key: 'config-keys', label: tx('插件参数', 'Plugin parameters'), children: selectedPlugin.config_keys.join(', ') }] : []),
            ]} />}
            {selectedDiagnostics.map((diagnostic, index) => <Alert key={`${diagnostic.component}:${index}`} type="error" showIcon
              message={`${diagnostic.component} · ${diagnostic.phase} · ${diagnostic.code}`} description={diagnostic.message} />)}
            {selectedPlugin?.error && !selectedDiagnostics.length && <Alert type="error" showIcon message={tx('加载失败原因', 'Load failure')} description={selectedPlugin.error} />}
            {selected.contribution && <Descriptions column={1} size="small" bordered items={[
              ...(selected.contribution.interface_id ? [{ key: 'interface_id', label: tx('插件接口', 'Plugin interface'), children: selected.contribution.interface_id }] : []),
              { key: 'plugin', label: tx('插件', 'Plugin'), children: selected.contribution.plugin },
              { key: 'stage', label: selected.contribution.mode === 'service' ? tx('默认阶段', 'Default phase') : tx('阶段', 'Stage'), children: `${stageLabel(selected.contribution.stage)} (${selected.contribution.stage})` },
              { key: 'mode', label: tx('类型', 'Mode'), children: modeLabel(selected.contribution.mode) },
              { key: 'order', label: tx('阶段内顺序', 'Stage order'), children: selected.contribution.mode === 'service' ? tx('按请求匹配，继承当前操作阶段', 'Matched on demand; inherits the active operation phase') : selected.contribution.order },
              { key: 'priority', label: tx('声明优先级', 'Priority'), children: selected.contribution.priority },
              { key: 'handler', label: tx('接口', 'Handler'), children: selected.contribution.handler },
              { key: 'before', label: tx('先于', 'Before'), children: selected.contribution.before.join(', ') || '—' },
              { key: 'after', label: tx('后于', 'After'), children: selected.contribution.after.join(', ') || '—' },
              { key: 'scope', label: tx('作用域', 'Scope'), children: selected.contribution.scope },
              { key: 'failure', label: tx('接口异常时', 'On failure'), children: selected.contribution.mode === 'service' ? tx('记录并交给领域调用方处理', 'Record and propagate to the domain caller') : selected.contribution.fail_closed ? tx('阻断执行', 'Block execution') : tx('记录并继续', 'Record and continue') },
            ]} />}
            {selected.stage && <>
              <p>{tx('允许的外部接口类型', 'Supported external modes')}</p>
              {selected.stage.supported_modes.map((mode) => <Tag key={mode}>{modeLabel(mode)}</Tag>)}
              <p>{tx('本阶段接口（按执行顺序）', 'Stage contributions in execution order')}</p>
              <div className="plugin-plan-detail-links">{selected.stage.contributions.slice().sort((a, b) => a.order - b.order).map((item) =>
                <Button key={item.id} size="small" type="link" onClick={() => selectNode(`contribution:${item.id}`)}>{item.order}. {item.id}</Button>)}
                {!selected.stage.contributions.length && <Typography.Text type="secondary">{tx('没有贡献接口', 'No contributions')}</Typography.Text>}</div>
            </>}
            {selected.kind === 'plugin' && <>
              <p>{tx(`领域接口 ${selectedPlugin?.interfaces.length || 0} 个；关联阶段与贡献：`, `${selectedPlugin?.interfaces.length || 0} domain interfaces. Associated phases and contributions:`)}</p>
              <div className="plugin-plan-detail-links">{catalog.filter((node) => node.contribution?.plugin === selectedPlugin?.name || node.kind === 'interface' && node.plugin?.name === selectedPlugin?.name).map((node) =>
                <Button key={node.id} size="small" type="link" onClick={() => selectNode(node.id)}>{node.contribution ? `${stageLabel(node.contribution.stage)} · ` : ''}{node.label}</Button>)}</div>
              <p>{tx('领域接口按请求执行，继承当前操作的公共阶段；独立调用采用默认阶段。发现成功不代表 provider 已实例化或通过健康检查。', 'Domain interfaces execute on demand and inherit the active operation phase; independent calls use their default phase. Discovery does not imply provider instantiation or health checks.')}</p>
            </>}
            {selected.kind === 'interface' && <p>{tx('该接口当前没有安装可用阶段绑定，请检查插件状态及接口声明。', 'No active stage binding is installed for this interface. Check its plugin status and declaration.')}</p>}
            {selected.kind === 'execution' && <p>{tx('由核心执行器调用；前后阶段的接口可观察或参与处理。', 'The core executor performs this operation; surrounding phases expose observation and processing hooks.')}</p>}
          </>}
        </aside>
      </div>
      {!!plan?.notifications?.length && <details ref={notifications}>
        <summary>{tx('独立通知（异常、取消、审核、后台）', 'Separate notifications (errors, cancellation, review, background)')}</summary>
        <Descriptions column={1} size="small" bordered items={plan.notifications.map((notification) => ({
          key: notification.stage, label: stageLabel(notification.stage),
          children: notification.contributions.filter((item) => !filter || item.plugin === filter).map((item) =>
            <Button key={item.id} size="small" type="link" title={`${item.handler} · ${item.mode} · ${item.order}`} onClick={() => selectNode(`contribution:${item.id}`)}>{item.id}</Button>),
        }))} />
      </details>}
      {replayVisible && plan && <PluginReplay plan={plan} en={en} onStep={onReplayStep} onSteps={onReplaySteps} locateStep={replayLocate} />}
    </div>
  </Drawer>;
};

export default PluginArchitecture;

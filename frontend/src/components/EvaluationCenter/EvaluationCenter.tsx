import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Checkbox, Col, Divider, Empty, Input, List, Modal, Popconfirm, Progress,
  Row, Select, Space, Statistic, Table, Tabs, Tag, Typography, message,
} from 'antd';
import {
  EyeOutlined, FileDoneOutlined, LineChartOutlined, PlayCircleOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { useUiStore } from '../../stores/uiStore';
import {
  archiveEvalBaseline, archiveEvalBatch, getEvalBatch, listEvalArchives, listEvalCases,
  getEvalBaseline, listEvalTrends, startEvalBatch,
  getEvalRepository,
  archiveEvalPerformanceResult, getEvalPerformanceResult, listEvalPerformanceResults,
  type EvalArchive, type EvalBaseline, type EvalBatch, type EvalCaseInfo, type EvalPerformanceRun,
  type EvalRepositoryInfo, type EvalResult, type EvalTrend,
} from '../../services/api';
import './EvaluationCenter.css';

const { Text } = Typography;
const EVAL_AGENT_LABEL = 'DevAgent';
const UNCLASSIFIED_SUITE = '__unclassified__';

function fmtDuration(ms: number): string {
  if (!ms) return '-';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function fmtTime(value: string): string {
  if (!value) return '-';
  return new Date(value).toLocaleString();
}

function archiveExecutionTimestamp(archive: EvalArchive): number {
  const timestamp = Date.parse(archive.started_at || archive.created_at);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function statusTag(status: string, passed?: boolean): React.ReactNode {
  if (status === 'passed' || passed) return <Tag color="success">通过</Tag>;
  if (status === 'failed') return <Tag color="error">失败</Tag>;
  if (status === 'timeout') return <Tag color="warning">超时</Tag>;
  if (status === 'running') return <Tag color="processing">运行中</Tag>;
  if (status === 'queued') return <Tag>排队中</Tag>;
  return <Tag>{status || '未知'}</Tag>;
}

function traceSessionFromResult(result: EvalResult): string | null {
  const path = result.trace_path || '';
  const fileName = path.split(/[\\/]/).pop() || '';
  if (fileName.startsWith('trace_') && fileName.endsWith('.jsonl')) {
    return fileName.slice('trace_'.length, -'.jsonl'.length);
  }
  return null;
}

type EvaluationTab = 'overview' | 'performance' | 'comparison' | 'runs' | 'archives';

const EvaluationCenter: React.FC = () => {
  const {
    evaluationVisible, setEvaluationVisible, setTraceSessionId, setTraceVisible,
  } = useUiStore();
  const [cases, setCases] = useState<EvalCaseInfo[]>([]);
  const [baseline, setBaseline] = useState<EvalBaseline | null>(null);
  const [repository, setRepository] = useState<EvalRepositoryInfo | null>(null);
  const [trends, setTrends] = useState<EvalTrend[]>([]);
  const [archives, setArchives] = useState<EvalArchive[]>([]);
  const [performanceRuns, setPerformanceRuns] = useState<EvalPerformanceRun[]>([]);
  const [performanceLoading, setPerformanceLoading] = useState(false);
  const [performanceArchiving, setPerformanceArchiving] = useState(false);
  const [selectedPerformance, setSelectedPerformance] = useState<EvalPerformanceRun | null>(null);
  const [performanceVersion, setPerformanceVersion] = useState('');
  const [performanceQuery, setPerformanceQuery] = useState('');
  const [performanceArchiveFilter, setPerformanceArchiveFilter] = useState<'all' | 'archived' | 'pending'>('all');
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [resultQuery, setResultQuery] = useState('');
  const [resultStatus, setResultStatus] = useState<'all' | 'passed' | 'failed' | 'timeout'>('all');
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [archiveQuery, setArchiveQuery] = useState('');
  const [activeTab, setActiveTab] = useState<EvaluationTab>('overview');
  const [selectedSuites, setSelectedSuites] = useState<string[]>([]);
  const [batch, setBatch] = useState<EvalBatch | null>(null);
  const [loading, setLoading] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const pollRef = useRef<number | null>(null);

  const suites = useMemo(() => Array.from(new Set(cases.map((item) => (
    item.metadata?.suite ? String(item.metadata.suite) : UNCLASSIFIED_SUITE
  )))), [cases]);
  const suiteOptions = useMemo(() => suites.map((value) => ({
    value,
    label: value === UNCLASSIFIED_SUITE ? '未分类' : value,
    count: cases.filter((item) => (item.metadata?.suite ? String(item.metadata.suite) : UNCLASSIFIED_SUITE) === value).length,
  })), [cases, suites]);
  const allSuitesSelected = suites.length > 0 && selectedSuites.length === suites.length;
  const selectedCaseIds = useMemo(() => cases
    .filter((item) => selectedSuites.includes(item.metadata?.suite ? String(item.metadata.suite) : UNCLASSIFIED_SUITE))
    .map((item) => item.id), [cases, selectedSuites]);
  const archiveBatchReady = !!batch
    && batch.status === 'completed'
    && cases.length > 0
    && batch.case_ids.length === cases.length
    && cases.every((item) => batch.case_ids.includes(item.id));
  const archiveBaselineReady = !batch && !!baseline && cases.length > 0 && baseline.case_count >= cases.length;
  const archiveReady = archiveBatchReady || archiveBaselineReady;
  const sortedArchives = useMemo(() => [...archives].sort(
    (left, right) => archiveExecutionTimestamp(right) - archiveExecutionTimestamp(left),
  ), [archives]);

  const filteredPerformanceRuns = useMemo(() => {
    const query = performanceQuery.trim().toLowerCase();
    return performanceRuns.filter((run) => {
      const matchesQuery = !query || [run.version, run.file_name, run.source_path, run.suite]
        .some((value) => value?.toLowerCase().includes(query));
      const matchesArchive = performanceArchiveFilter === 'all'
        || (performanceArchiveFilter === 'archived' ? run.archived : !run.archived);
      return matchesQuery && matchesArchive;
    });
  }, [performanceArchiveFilter, performanceQuery, performanceRuns]);

  const filteredArchives = useMemo(() => {
    const query = archiveQuery.trim().toLowerCase();
    if (!query) return sortedArchives;
    return sortedArchives.filter((archive) => [
      archive.version, archive.suite, archive.note, archive.archive_id,
    ].some((value) => value?.toLowerCase().includes(query)));
  }, [archiveQuery, sortedArchives]);

  const filteredSelectedResults = useMemo(() => {
    const results = selectedPerformance?.results || [];
    const query = resultQuery.trim().toLowerCase();
    return results.filter((result) => {
      const matchesQuery = !query || [result.case_id, result.model, result.error]
        .some((value) => value?.toLowerCase().includes(query));
      const matchesStatus = resultStatus === 'all'
        || (resultStatus === 'passed'
          ? result.passed
          : resultStatus === 'timeout'
            ? result.status === 'timeout'
            : !result.passed && result.status !== 'timeout');
      return matchesQuery && matchesStatus;
    });
  }, [resultQuery, resultStatus, selectedPerformance]);

  const selectedCase = useMemo(
    () => filteredSelectedResults.find((result) => result.case_id === selectedCaseId)
      || selectedPerformance?.results?.find((result) => result.case_id === selectedCaseId)
      || null,
    [filteredSelectedResults, selectedCaseId, selectedPerformance],
  );

  const comparisonRuns = useMemo(
    () => performanceRuns.filter((run) => comparisonIds.includes(run.result_id)),
    [comparisonIds, performanceRuns],
  );

  useEffect(() => {
    setSelectedSuites((current) => current.length > 0
      ? current.filter((value) => suites.includes(value))
      : suites);
  }, [suites]);

  const refresh = async () => {
    setLoading(true);
    try {
      const [caseList, trendList, archiveList, performanceList] = await Promise.all([
        listEvalCases(), listEvalTrends(), listEvalArchives(), listEvalPerformanceResults(),
      ]);
      setCases(caseList);
      setTrends(trendList);
      setArchives(archiveList);
      setPerformanceRuns(performanceList);
      setRepository(await getEvalRepository());
      try {
        setBaseline(await getEvalBaseline());
      } catch {
        setBaseline(null);
      }
    } catch (error: any) {
      message.error(`评测数据加载失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setLoading(false);
    }
  };

  const openPerformance = async () => {
    setActiveTab('performance');
    setPerformanceLoading(true);
    try {
      setPerformanceRuns(await listEvalPerformanceResults());
    } catch (error: any) {
      message.error(`性能结果加载失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setPerformanceLoading(false);
    }
  };

  const selectPerformance = async (run: EvalPerformanceRun) => {
    setPerformanceLoading(true);
    setSelectedCaseId(null);
    try {
      const detail = await getEvalPerformanceResult(run.result_id);
      setSelectedPerformance(detail);
      setPerformanceVersion(detail.version);
    } catch (error: any) {
      message.error(`性能结果详情加载失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setPerformanceLoading(false);
    }
  };

  const openTrace = (result: EvalResult) => {
    const sessionId = traceSessionFromResult(result);
    if (!sessionId) {
      message.warning('该用例没有可直达的 Trace 文件');
      return;
    }
    setTraceSessionId(sessionId);
    setTraceVisible(true);
  };

  const archivePerformance = async () => {
    if (!selectedPerformance || !selectedPerformance.results?.length) return;
    const version = performanceVersion.trim();
    if (!version) {
      message.warning('请先填写归档版本');
      return;
    }
    setPerformanceArchiving(true);
    try {
      await archiveEvalPerformanceResult(selectedPerformance.result_id, version, `${version} 性能评测归档`);
      await refresh();
      setSelectedPerformance({ ...selectedPerformance, version, archived: true });
      message.success('性能评测已归档');
    } catch (error: any) {
      message.error(`性能评测归档失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setPerformanceArchiving(false);
    }
  };

  useEffect(() => {
    if (evaluationVisible) refresh();
    return () => {
      if (pollRef.current !== null) window.clearTimeout(pollRef.current);
    };
  }, [evaluationVisible]);

  const pollBatch = async (batchId: string) => {
    try {
      const next = await getEvalBatch(batchId);
      setBatch(next);
      if (next.status === 'queued' || next.status === 'running') {
        pollRef.current = window.setTimeout(() => pollBatch(batchId), 2000);
      } else {
        await refresh();
        message.success(`评测批次完成：${next.summary.passed}/${next.summary.completed} 通过`);
      }
    } catch (error: any) {
      message.error(`评测状态查询失败：${error?.response?.data?.detail || error.message || error}`);
    }
  };

  const runBatch = async () => {
    if (!repository?.version || repository.version === 'unknown') {
      message.warning('无法获取当前仓库版本，暂时不能启动评测');
      return;
    }
    if (!selectedCaseIds.length) {
      message.warning('请至少选择一个评测集');
      return;
    }
    setLoading(true);
    try {
      const next = await startEvalBatch({
        suite: allSuitesSelected ? 'all' : selectedSuites.join(', '),
        case_ids: selectedCaseIds,
        version: repository.version,
      });
      setBatch(next);
      setActiveTab('overview');
      pollBatch(next.batch_id);
    } catch (error: any) {
      message.error(`启动评测失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setLoading(false);
    }
  };

  const archiveBatch = async () => {
    if (batch && ['running', 'queued'].includes(batch.status)) return;
    if (!archiveReady) {
      message.warning('只有完整评测集全部执行完成后，才允许一键归档');
      return;
    }
    setArchiving(true);
    try {
      if (batch) await archiveEvalBatch(batch.batch_id, `${batch.version} ${batch.suite} 评测归档`);
      else if (baseline) await archiveEvalBaseline(`${baseline.version} DevAgent 基线归档`);
      await refresh();
      message.success('评测结果已归档');
    } catch (error: any) {
      message.error(`归档失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setArchiving(false);
    }
  };

  const resultColumns = [
    { title: '用例', dataIndex: 'case_id', key: 'case_id', ellipsis: true },
    { title: 'Agent', dataIndex: 'agent', key: 'agent', width: 100, render: () => EVAL_AGENT_LABEL },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90, render: (v: string, row: EvalResult) => statusTag(v, row.passed) },
    { title: '得分', dataIndex: 'score', key: 'score', width: 80, render: (v: number) => `${(v * 100).toFixed(0)}%` },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', width: 90, render: fmtDuration },
    { title: '模型', dataIndex: 'model', key: 'model', width: 130, ellipsis: true },
    { title: 'Trace', key: 'trace', width: 110, render: (_: unknown, row: EvalResult) => (
      <Space size={4}>
        <Text type="secondary" ellipsis style={{ maxWidth: 80 }}>{row.trace_id || '-'}</Text>
        {traceSessionFromResult(row) ? <Button size="small" type="link" onClick={(event) => { event.stopPropagation(); openTrace(row); }}>直达</Button> : null}
      </Space>
    ) },
  ];

  const activeSummary = batch?.summary;
  const renderBaseline = () => baseline ? (
    <Card size="small" className="evaluation-baseline-card" title={<Space><LineChartOutlined />性能基线</Space>} extra={<Space><Tag color="blue">{EVAL_AGENT_LABEL}</Tag><Text type="secondary">{baseline.version}</Text></Space>}>
      <div className="evaluation-baseline-meta">{baseline.label} · {baseline.model} · 快照时间：{fmtTime(baseline.captured_at)}</div>
      <Row gutter={[12, 12]} className="evaluation-stat-row">
        <Col xs={12} sm={8} md={4}><Statistic title="用例数" value={baseline.case_count} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="通过率" value={baseline.pass_rate} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="平均得分" value={baseline.average_score} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="通过 / 失败 / 超时" value={`${baseline.passed} / ${baseline.failed} / ${baseline.timeout}`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="累积耗时" value={fmtDuration(baseline.total_duration_ms)} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="总 Token" value={baseline.total_tokens} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="工具调用" value={baseline.total_tool_calls} /></Col>
      </Row>
      <Table size="small" rowKey="name" pagination={false} columns={[
        { title: '范围', dataIndex: 'name', key: 'name' },
        { title: '通过', key: 'passed', render: (_: unknown, row: EvalBaseline['groups'][number]) => `${row.passed} / ${row.total}` },
        { title: '通过率', dataIndex: 'pass_rate', key: 'pass_rate', render: (v: number) => `${(v * 100).toFixed(1)}%` },
        { title: '平均得分', dataIndex: 'average_score', key: 'average_score', render: (v: number) => `${(v * 100).toFixed(1)}%` },
        { title: '失败 / 超时', key: 'issues', render: (_: unknown, row: EvalBaseline['groups'][number]) => `${row.failed} / ${row.timeout}` },
      ]} dataSource={baseline.groups} />
    </Card>
  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无性能基线" />;

  const renderBatch = () => batch ? (
    <Card size="small" title="当前评测批次">
      <div className="evaluation-batch-line"><Text strong>{batch.version}</Text> · {batch.suite} · {statusTag(batch.status)}{batch.current_case_id ? <Text type="secondary">当前：{batch.current_case_id}</Text> : null}<Text type="secondary">开始：{fmtTime(batch.started_at)}</Text></div>
      {batch.status === 'running' || batch.status === 'queued' ? <Progress percent={batch.summary.total ? Math.round(batch.summary.completed / batch.summary.total * 100) : 0} status="active" /> : null}
      {batch.error ? <Alert type="error" showIcon message={batch.error} /> : null}
      <Row gutter={[12, 12]} className="evaluation-stat-row">
        <Col xs={12} sm={8} md={4}><Statistic title="总用例" value={activeSummary?.total || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="已完成" value={activeSummary?.completed || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="通过数" value={activeSummary?.passed || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="失败数" value={activeSummary?.failed || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="超时数" value={activeSummary?.timeout || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="错误数" value={activeSummary?.errors || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="通过率" value={activeSummary?.pass_rate || 0} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="平均得分" value={activeSummary?.average_score || 0} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="平均耗时" value={fmtDuration(activeSummary?.average_duration_ms || 0)} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="总 Token" value={activeSummary?.total_tokens || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="工具调用" value={activeSummary?.total_tool_calls || 0} /></Col>
      </Row>
      <Table size="small" rowKey="case_id" pagination={{ pageSize: 8 }} columns={resultColumns} dataSource={batch.results} onRow={(row) => ({ onClick: () => setSelectedCaseId(row.case_id) })} />
    </Card>
  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未运行评测批次" />;

  const performanceColumns = [
    { title: '版本', dataIndex: 'version', key: 'version', render: (v: string) => v || <Text type="secondary">未标记</Text> },
    { title: '结果文件', dataIndex: 'source_path', key: 'source_path', ellipsis: true },
    { title: '执行时间', dataIndex: 'started_at', key: 'started_at', render: fmtTime },
    { title: '用例数', dataIndex: 'result_count', key: 'result_count', width: 75 },
    { title: '通过率', key: 'pass_rate', render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.pass_rate * 100).toFixed(1)}%` },
    { title: '平均得分', key: 'score', render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.average_score * 100).toFixed(1)}%` },
    { title: '平均耗时', key: 'duration', render: (_: unknown, row: EvalPerformanceRun) => fmtDuration(row.summary.average_duration_ms) },
    { title: 'Token', key: 'tokens', render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tokens },
    { title: '归档', key: 'archived', render: (_: unknown, row: EvalPerformanceRun) => row.archived ? <Tag color="success">已归档</Tag> : <Tag>未归档</Tag> },
    { title: '操作', key: 'action', width: 90, render: (_: unknown, row: EvalPerformanceRun) => <Button size="small" onClick={(event) => { event.stopPropagation(); selectPerformance(row); }}>查看</Button> },
  ];

  const renderFailureDetail = () => selectedCase ? (
    <Card size="small" className="evaluation-case-detail" title={<Space>用例钻取：{selectedCase.case_id}{statusTag(selectedCase.status, selectedCase.passed)}</Space>}>
      {selectedCase.error ? <Alert type="error" showIcon message={selectedCase.error} /> : null}
      <div className="evaluation-checker-list">{(selectedCase.checker_results || []).length === 0 ? <Text type="secondary">没有 checker 诊断信息</Text> : (
        <List size="small" dataSource={selectedCase.checker_results} renderItem={(checker, index) => (
          <List.Item><Text>{String(checker.name || checker.checker || `checker-${index + 1}`)}</Text><Text type={checker.passed === false ? 'danger' : 'secondary'}>{checker.message || checker.detail || (checker.passed === false ? '未通过' : '通过')}</Text></List.Item>
        )} />
      )}</div>
      {traceSessionFromResult(selectedCase) ? <Button icon={<EyeOutlined />} onClick={() => openTrace(selectedCase)}>打开 Trace</Button> : null}
    </Card>
  ) : null;

  const renderPerformance = () => (
    <>
      <Alert type="info" showIcon message="先查看独立 performance JSONL 结果，再由你决定是否生成正式归档快照。" description="原始结果不会被覆盖；归档后会出现在‘已归档’页签，并按评测执行时间倒序展示。" style={{ marginBottom: 12 }} />
      <Space wrap className="evaluation-filter-row"><Input.Search allowClear value={performanceQuery} onChange={(event) => setPerformanceQuery(event.target.value)} placeholder="搜索版本、结果文件、评测集" style={{ width: 280 }} /><Select value={performanceArchiveFilter} onChange={setPerformanceArchiveFilter} style={{ width: 130 }} options={[{ value: 'all', label: '全部结果' }, { value: 'pending', label: '待归档' }, { value: 'archived', label: '已归档' }]} /><Text type="secondary">已加载 {filteredPerformanceRuns.length} / {performanceRuns.length} 个结果</Text></Space>
      <Table size="small" rowKey="result_id" loading={performanceLoading} dataSource={filteredPerformanceRuns} pagination={{ pageSize: 6 }} rowSelection={{ selectedRowKeys: comparisonIds, onChange: (keys) => setComparisonIds(keys as string[]) }} rowClassName={(row) => selectedPerformance?.result_id === row.result_id ? 'evaluation-selected-row' : ''} onRow={(row) => ({ onClick: () => selectPerformance(row) })} columns={performanceColumns} />
      {selectedPerformance && (
        <Card size="small" title={<Space>{selectedPerformance.version || selectedPerformance.file_name}{selectedPerformance.archived ? <Tag color="success">已归档</Tag> : <Tag>待归档</Tag>}</Space>} style={{ marginTop: 12 }} extra={!selectedPerformance.archived ? (
          <Popconfirm title="确认归档这份性能结果？" description={`版本：${performanceVersion || '未填写'}，用例数：${selectedPerformance.summary.total}`} okText="确认归档" cancelText="取消" onConfirm={archivePerformance}><Button type="primary" icon={<FileDoneOutlined />} loading={performanceArchiving}>确认归档</Button></Popconfirm>
        ) : null}>
          <Row gutter={[12, 12]} className="evaluation-stat-row">
            <Col xs={12} sm={8} md={4}><Statistic title="用例数" value={selectedPerformance.summary.total} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="通过率" value={selectedPerformance.summary.pass_rate} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="平均得分" value={selectedPerformance.summary.average_score} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="平均耗时" value={fmtDuration(selectedPerformance.summary.average_duration_ms)} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="总 Token" value={selectedPerformance.summary.total_tokens} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="工具调用" value={selectedPerformance.summary.total_tool_calls} /></Col>
          </Row>
          {!selectedPerformance.archived ? <Space direction="vertical" style={{ width: '100%', marginBottom: 12 }}><Text type="secondary">归档版本（可修改）</Text><Input value={performanceVersion} onChange={(event) => setPerformanceVersion(event.target.value)} placeholder="例如 3.3.1" /></Space> : null}
          <Space wrap className="evaluation-filter-row"><Input.Search allowClear value={resultQuery} onChange={(event) => setResultQuery(event.target.value)} placeholder="搜索用例、模型、错误信息" style={{ width: 280 }} /><Select value={resultStatus} onChange={setResultStatus} style={{ width: 120 }} options={[{ value: 'all', label: '全部用例' }, { value: 'failed', label: '失败' }, { value: 'timeout', label: '超时' }, { value: 'passed', label: '通过' }]} /><Text type="secondary">显示 {filteredSelectedResults.length} / {selectedPerformance.results?.length || 0}</Text></Space>
          <Table size="small" rowKey="case_id" pagination={{ pageSize: 8 }} columns={resultColumns} dataSource={filteredSelectedResults} scroll={{ x: 950 }} onRow={(row) => ({ onClick: () => setSelectedCaseId(row.case_id) })} />
          {renderFailureDetail()}
        </Card>
      )}
    </>
  );

  const renderComparison = () => (
    <>
      <Alert type="info" showIcon message="多版本对比" description="在性能结果页勾选两份或多份结果后，这里会对比执行时间、通过率、得分、Token 和工具调用。" style={{ marginBottom: 12 }} />
      {comparisonRuns.length < 2 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请先在‘性能结果’页勾选至少两个版本" /> : (
        <Table size="small" rowKey="result_id" pagination={false} dataSource={comparisonRuns} columns={[
          { title: '版本', dataIndex: 'version', key: 'version', render: (v: string, row: EvalPerformanceRun) => v || row.file_name },
          { title: '执行时间', dataIndex: 'started_at', key: 'started_at', render: fmtTime },
          { title: '用例数', dataIndex: 'result_count', key: 'result_count' },
          { title: '通过率', key: 'pass_rate', render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.pass_rate * 100).toFixed(1)}%` },
          { title: '平均得分', key: 'score', render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.average_score * 100).toFixed(1)}%` },
          { title: '平均耗时', key: 'duration', render: (_: unknown, row: EvalPerformanceRun) => fmtDuration(row.summary.average_duration_ms) },
          { title: 'Token', key: 'tokens', render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tokens },
          { title: '工具调用', key: 'tools', render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tool_calls },
        ]} />
      )}
    </>
  );

  const renderRuns = () => trends.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无批次记录" /> : (
    <List size="small" dataSource={trends} renderItem={(item) => (
      <List.Item><List.Item.Meta title={<Space>{item.version}{statusTag(item.status)}<Text type="secondary">{item.suite}</Text></Space>} description={fmtTime(item.started_at)} /><Space className="evaluation-trend-values"><Text>通过率 {(item.summary.pass_rate * 100).toFixed(1)}%</Text><Text>得分 {(item.summary.average_score * 100).toFixed(1)}%</Text><Text>完成 {item.summary.completed}/{item.summary.total}</Text><Text>失败 {item.summary.failed}</Text><Text>超时 {item.summary.timeout}</Text><Text>耗时 {fmtDuration(item.summary.average_duration_ms)}</Text><Text>Token {item.summary.total_tokens}</Text><Text>工具 {item.summary.total_tool_calls}</Text></Space></List.Item>
    )} />
  );

  const renderArchives = () => (
    <>
      <Space wrap className="evaluation-filter-row"><Input.Search allowClear value={archiveQuery} onChange={(event) => setArchiveQuery(event.target.value)} placeholder="搜索版本、评测集、归档说明" style={{ width: 300 }} /><Text type="secondary">最新执行的归档展示在最上面</Text></Space>
      {filteredArchives.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无归档快照" /> : (
        <Table size="small" rowKey="archive_id" pagination={{ pageSize: 5 }} dataSource={filteredArchives} columns={[
          { title: '版本', dataIndex: 'version', key: 'version' },
          { title: '评测集', dataIndex: 'suite', key: 'suite' },
          { title: '通过率', key: 'rate', render: (_: unknown, r: EvalArchive) => `${(r.summary.pass_rate * 100).toFixed(1)}%` },
          { title: '平均得分', key: 'score', render: (_: unknown, r: EvalArchive) => `${(r.summary.average_score * 100).toFixed(1)}%` },
          { title: '失败/超时/错误', key: 'issues', render: (_: unknown, r: EvalArchive) => `${r.summary.failed} / ${r.summary.timeout} / ${r.summary.errors}` },
          { title: '平均耗时', key: 'duration', render: (_: unknown, r: EvalArchive) => fmtDuration(r.summary.average_duration_ms) },
          { title: 'Token', key: 'tokens', render: (_: unknown, r: EvalArchive) => r.summary.total_tokens },
          { title: '评测执行时间', dataIndex: 'started_at', key: 'started_at', render: fmtTime },
          { title: '归档 ID', dataIndex: 'archive_id', key: 'archive_id', ellipsis: true },
        ]} scroll={{ x: 1050 }} />
      )}
    </>
  );

  return (
    <Modal title={<Space><LineChartOutlined />ArchitectCoder 能力基准中心</Space>} open={evaluationVisible} onCancel={() => setEvaluationVisible(false)} footer={null} width={1220} styles={{ body: { maxHeight: 'calc(100vh - 150px)', overflowY: 'auto' } }}>
      <Card size="small" className="evaluation-control-card">
        <Space wrap>
          <Tag color="blue">{EVAL_AGENT_LABEL}</Tag>
          <Select
            mode="multiple"
            value={selectedSuites}
            onChange={setSelectedSuites}
            style={{ minWidth: 280, maxWidth: 420 }}
            maxTagCount="responsive"
            placeholder="选择评测集"
            disabled={!suites.length}
            options={suiteOptions.map((item) => ({ value: item.value, label: `${item.label} (${item.count})` }))}
            dropdownRender={(menu) => (
              <>
                {menu}
                <Divider style={{ margin: '8px 0' }} />
                <div className="evaluation-suite-select-all" onMouseDown={(event) => event.preventDefault()}>
                  <Checkbox
                    checked={allSuitesSelected}
                    indeterminate={selectedSuites.length > 0 && !allSuitesSelected}
                    onChange={(event) => setSelectedSuites(event.target.checked ? suites : [])}
                  >
                    全选评测集（{suites.length} 类）
                  </Checkbox>
                </div>
              </>
            )}
          />
          <Tag color={selectedCaseIds.length === cases.length && cases.length > 0 ? 'green' : 'blue'}>{selectedCaseIds.length} / {cases.length || '-'} 用例</Tag>
          <Tag color={repository?.dirty ? 'warning' : 'green'} title={repository?.commit || undefined}>{repository ? `${repository.branch}@${repository.commit.slice(0, 12)}${repository.dirty ? ' · dirty' : ''}` : '读取仓库版本中…'}</Tag>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={runBatch} loading={loading} disabled={!selectedCaseIds.length || !repository?.version || repository.version === 'unknown' || !!batch && ['running', 'queued'].includes(batch.status)}>一键运行</Button>
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>刷新</Button>
          <Button icon={<EyeOutlined />} onClick={openPerformance}>性能结果</Button>
          <Popconfirm title="确认归档当前完整评测？" description="只有全部评测集执行完成后才允许归档，原始结果不会被覆盖。" okText="确认归档" cancelText="取消" onConfirm={archiveBatch}><Button icon={<FileDoneOutlined />} loading={archiving} disabled={!archiveReady}>一键归档</Button></Popconfirm>
        </Space>
        <div className="evaluation-hint">支持组合评测集运行；“全选”会执行当前目录下全部评测用例。只有全部用例完成后才允许一键归档。版本自动取当前 Git 分支和 HEAD commit。</div>
      </Card>
      <Tabs activeKey={activeTab} onChange={(key) => setActiveTab(key as EvaluationTab)} items={[
        { key: 'overview', label: '概览与当前批次', children: <>{renderBaseline()}<Divider orientation="left">当前批次</Divider>{renderBatch()}</> },
        { key: 'performance', label: `性能结果 (${performanceRuns.length})`, children: renderPerformance() },
        { key: 'comparison', label: `多版本对比${comparisonRuns.length ? ` (${comparisonRuns.length})` : ''}`, children: renderComparison() },
        { key: 'runs', label: '版本趋势', children: renderRuns() },
        { key: 'archives', label: `已归档 (${archives.length})`, children: renderArchives() },
      ]} />
    </Modal>
  );
};

export default EvaluationCenter;

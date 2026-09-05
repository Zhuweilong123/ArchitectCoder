import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Col, Divider, Empty, Input, List, Modal, Progress,
  Row, Select, Space, Statistic, Table, Tag, Typography, message,
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
  type EvalRepositoryInfo, type EvalTrend,
} from '../../services/api';
import './EvaluationCenter.css';

const { Text } = Typography;
const EVAL_AGENT_LABEL = 'DevAgent';

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

const EvaluationCenter: React.FC = () => {
  const { evaluationVisible, setEvaluationVisible } = useUiStore();
  const [cases, setCases] = useState<EvalCaseInfo[]>([]);
  const [baseline, setBaseline] = useState<EvalBaseline | null>(null);
  const [repository, setRepository] = useState<EvalRepositoryInfo | null>(null);
  const [trends, setTrends] = useState<EvalTrend[]>([]);
  const [archives, setArchives] = useState<EvalArchive[]>([]);
  const [performanceRuns, setPerformanceRuns] = useState<EvalPerformanceRun[]>([]);
  const [performanceVisible, setPerformanceVisible] = useState(false);
  const [performanceLoading, setPerformanceLoading] = useState(false);
  const [performanceArchiving, setPerformanceArchiving] = useState(false);
  const [selectedPerformance, setSelectedPerformance] = useState<EvalPerformanceRun | null>(null);
  const [performanceVersion, setPerformanceVersion] = useState('');
  const [suite, setSuite] = useState('baseline');
  const [batch, setBatch] = useState<EvalBatch | null>(null);
  const [loading, setLoading] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const pollRef = useRef<number | null>(null);

  const suites = useMemo(() => Array.from(new Set(cases
    .map((item) => item.metadata?.suite)
    .filter(Boolean))) as string[], [cases]);
  const sortedArchives = useMemo(() => [...archives].sort(
    (left, right) => archiveExecutionTimestamp(right) - archiveExecutionTimestamp(left),
  ), [archives]);

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
    setPerformanceVisible(true);
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

  const archivePerformance = async () => {
    if (!selectedPerformance || !selectedPerformance.results?.length) return;
    setPerformanceArchiving(true);
    try {
      await archiveEvalPerformanceResult(
        selectedPerformance.result_id,
        performanceVersion,
        `${performanceVersion || selectedPerformance.file_name} 性能评测归档`,
      );
      await refresh();
      const updated = await listEvalPerformanceResults();
      setPerformanceRuns(updated);
      setSelectedPerformance({ ...selectedPerformance, archived: true });
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
    setLoading(true);
    try {
      const next = await startEvalBatch({ suite, version: repository.version });
      setBatch(next);
      pollBatch(next.batch_id);
    } catch (error: any) {
      message.error(`启动评测失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setLoading(false);
    }
  };

  const archiveBatch = async () => {
    if (batch && ['running', 'queued'].includes(batch.status)) return;
    if (!batch && !baseline) return;
    setArchiving(true);
    try {
      if (batch) {
        await archiveEvalBatch(batch.batch_id, `${batch.version} ${batch.suite} 评测归档`);
      } else if (baseline) {
        await archiveEvalBaseline(`${baseline.version} DevAgent 基线归档`);
      }
      await refresh();
      message.success('评测结果已归档');
    } catch (error: any) {
      message.error(`归档失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setArchiving(false);
    }
  };

  const activeSummary = batch?.summary;
  const resultColumns = [
    { title: '用例', dataIndex: 'case_id', key: 'case_id', ellipsis: true },
    { title: 'Agent', dataIndex: 'agent', key: 'agent', width: 100, render: () => EVAL_AGENT_LABEL },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90, render: (v: string, row: any) => statusTag(v, row.passed) },
    { title: '得分', dataIndex: 'score', key: 'score', width: 80, render: (v: number) => `${(v * 100).toFixed(0)}%` },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', width: 90, render: fmtDuration },
    { title: '模型', dataIndex: 'model', key: 'model', width: 130, ellipsis: true },
    { title: 'Trace', dataIndex: 'trace_id', key: 'trace_id', width: 140, ellipsis: true },
  ];

  return (
    <Modal
      title={<Space><LineChartOutlined />ArchitectCoder 能力基准中心</Space>}
      open={evaluationVisible}
      onCancel={() => setEvaluationVisible(false)}
      footer={null}
      width={1180}
      styles={{ body: { maxHeight: 'calc(100vh - 150px)', overflowY: 'auto' } }}
    >
      <Card size="small" className="evaluation-control-card">
        <Space wrap>
          <Tag color="blue">{EVAL_AGENT_LABEL}</Tag>
          <Select value={suite} onChange={setSuite} style={{ width: 180 }} options={suites.map((v) => ({ value: v, label: v }))} />
          <Tag color={repository?.dirty ? 'warning' : 'green'} title={repository?.commit || undefined}>
            {repository ? `${repository.branch}@${repository.commit.slice(0, 12)}${repository.dirty ? ' · dirty' : ''}` : '读取仓库版本中…'}
          </Tag>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={runBatch} loading={loading} disabled={!suite || !repository?.version || repository.version === 'unknown' || !!batch && ['running', 'queued'].includes(batch.status)}>
            一键运行
          </Button>
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>刷新</Button>
          <Button icon={<EyeOutlined />} onClick={openPerformance}>性能结果</Button>
          <Button icon={<FileDoneOutlined />} onClick={archiveBatch} loading={archiving} disabled={(!batch && !baseline) || !!batch && ['running', 'queued'].includes(batch.status)}>
            一键归档
          </Button>
        </Space>
        <div className="evaluation-hint">仅运行完整 DevAgent 生产链路；版本自动取当前 Git 分支和 HEAD commit，评测将在隔离工作区顺序执行并用于趋势对比和归档检索。dirty 表示工作区存在未提交修改。</div>
      </Card>

      {baseline && (
        <Card
          size="small"
          className="evaluation-baseline-card"
          title={<Space><LineChartOutlined />性能基线</Space>}
          extra={<Space><Tag color="blue">{EVAL_AGENT_LABEL}</Tag><Text type="secondary">{baseline.version}</Text></Space>}
        >
          <div className="evaluation-baseline-meta">
            {baseline.label} · {baseline.model} · 快照时间：{fmtTime(baseline.captured_at)}
          </div>
          <Row gutter={[12, 12]} className="evaluation-stat-row">
            <Col xs={12} sm={8} md={4}><Statistic title="用例数" value={baseline.case_count} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="通过率" value={baseline.pass_rate} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="平均得分" value={baseline.average_score} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="通过 / 失败 / 超时" value={`${baseline.passed} / ${baseline.failed} / ${baseline.timeout}`} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="累积耗时" value={fmtDuration(baseline.total_duration_ms)} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="总 Token" value={baseline.total_tokens} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="工具调用" value={baseline.total_tool_calls} /></Col>
          </Row>
          <Table
            size="small"
            rowKey="name"
            pagination={false}
            columns={[
              { title: '范围', dataIndex: 'name', key: 'name' },
              { title: '通过', key: 'passed', render: (_: unknown, row: EvalBaseline['groups'][number]) => `${row.passed} / ${row.total}` },
              { title: '通过率', dataIndex: 'pass_rate', key: 'pass_rate', render: (v: number) => `${(v * 100).toFixed(1)}%` },
              { title: '平均得分', dataIndex: 'average_score', key: 'average_score', render: (v: number) => `${(v * 100).toFixed(1)}%` },
              { title: '失败 / 超时', key: 'issues', render: (_: unknown, row: EvalBaseline['groups'][number]) => `${row.failed} / ${row.timeout}` },
            ]}
            dataSource={baseline.groups}
          />
        </Card>
      )}

      {batch && (
        <>
          <div className="evaluation-batch-line">
            <Text strong>{batch.version}</Text> · {batch.suite} · {statusTag(batch.status)}
            {batch.current_case_id ? <Text type="secondary">当前：{batch.current_case_id}</Text> : null}
            <Text type="secondary">开始：{fmtTime(batch.started_at)}</Text>
          </div>
          {batch.status === 'running' || batch.status === 'queued' ? (
            <Progress percent={batch.summary.total ? Math.round(batch.summary.completed / batch.summary.total * 100) : 0} status="active" />
          ) : null}
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
          <Table size="small" rowKey="case_id" pagination={{ pageSize: 8 }} columns={resultColumns} dataSource={batch.results} />
        </>
      )}

      <Modal
        title={<Space><EyeOutlined />性能评测结果</Space>}
        open={performanceVisible}
        onCancel={() => setPerformanceVisible(false)}
        footer={null}
        width={1220}
        styles={{ body: { maxHeight: 'calc(100vh - 180px)', overflowY: 'auto' } }}
      >
        <Alert
          type="info"
          showIcon
          message="先查看独立 performance JSONL 结果，再由你决定是否生成正式归档快照。"
          description="归档后会写入 temp/evals/archives，并出现在下方的‘已归档’列表中；原始结果文件不会被覆盖。"
          style={{ marginBottom: 12 }}
        />
        <Table
          size="small"
          rowKey="result_id"
          loading={performanceLoading}
          dataSource={performanceRuns}
          pagination={{ pageSize: 6 }}
          rowClassName={(row) => selectedPerformance?.result_id === row.result_id ? 'evaluation-selected-row' : ''}
          columns={[
            { title: '版本', dataIndex: 'version', key: 'version', render: (v: string) => v || <Text type="secondary">未标记</Text> },
            { title: '结果文件', dataIndex: 'source_path', key: 'source_path', ellipsis: true },
            { title: '执行时间', dataIndex: 'started_at', key: 'started_at', render: fmtTime },
            { title: '用例数', dataIndex: 'result_count', key: 'result_count', width: 75 },
            { title: '通过率', key: 'pass_rate', render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.pass_rate * 100).toFixed(1)}%` },
            { title: '平均得分', key: 'score', render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.average_score * 100).toFixed(1)}%` },
            { title: '平均耗时', key: 'duration', render: (_: unknown, row: EvalPerformanceRun) => fmtDuration(row.summary.average_duration_ms) },
            { title: 'Token', key: 'tokens', render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tokens },
            { title: '归档', key: 'archived', render: (_: unknown, row: EvalPerformanceRun) => row.archived ? <Tag color="success">已归档</Tag> : <Tag>未归档</Tag> },
            { title: '操作', key: 'action', width: 90, render: (_: unknown, row: EvalPerformanceRun) => <Button size="small" onClick={() => selectPerformance(row)}>查看</Button> },
          ]}
        />

        {selectedPerformance && (
          <Card
            size="small"
            title={<Space>{selectedPerformance.version || selectedPerformance.file_name}{selectedPerformance.archived ? <Tag color="success">已归档</Tag> : <Tag>待归档</Tag>}</Space>}
            style={{ marginTop: 12 }}
            extra={!selectedPerformance.archived && <Button type="primary" icon={<FileDoneOutlined />} onClick={archivePerformance} loading={performanceArchiving}>确认归档</Button>}
          >
            <Row gutter={[12, 12]} className="evaluation-stat-row">
              <Col xs={12} sm={8} md={4}><Statistic title="用例数" value={selectedPerformance.summary.total} /></Col>
              <Col xs={12} sm={8} md={4}><Statistic title="通过率" value={selectedPerformance.summary.pass_rate} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
              <Col xs={12} sm={8} md={4}><Statistic title="平均得分" value={selectedPerformance.summary.average_score} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
              <Col xs={12} sm={8} md={4}><Statistic title="平均耗时" value={fmtDuration(selectedPerformance.summary.average_duration_ms)} /></Col>
              <Col xs={12} sm={8} md={4}><Statistic title="总 Token" value={selectedPerformance.summary.total_tokens} /></Col>
              <Col xs={12} sm={8} md={4}><Statistic title="工具调用" value={selectedPerformance.summary.total_tool_calls} /></Col>
            </Row>
            {!selectedPerformance.archived && (
              <Space direction="vertical" style={{ width: '100%', marginBottom: 12 }}>
                <Text type="secondary">归档版本（可修改）</Text>
                <Input value={performanceVersion} onChange={(event) => setPerformanceVersion(event.target.value)} placeholder="例如 3.3.1" />
              </Space>
            )}
            <Table size="small" rowKey="case_id" pagination={{ pageSize: 8 }} columns={resultColumns} dataSource={selectedPerformance.results || []} scroll={{ x: 950 }} />
          </Card>
        )}
      </Modal>

      <Divider orientation="left">版本趋势</Divider>
      {trends.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无批次记录" /> : (
        <List
          size="small"
          dataSource={trends}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta title={<Space>{item.version}{statusTag(item.status)}<Text type="secondary">{item.suite}</Text></Space>} description={fmtTime(item.started_at)} />
              <Space className="evaluation-trend-values">
                <Text>通过率 {(item.summary.pass_rate * 100).toFixed(1)}%</Text>
                <Text>得分 {(item.summary.average_score * 100).toFixed(1)}%</Text>
                <Text>完成 {item.summary.completed}/{item.summary.total}</Text>
                <Text>通过 {item.summary.passed}/{item.summary.total}</Text>
                <Text>失败 {item.summary.failed}</Text>
                <Text>超时 {item.summary.timeout}</Text>
                <Text>错误 {item.summary.errors}</Text>
                <Text>耗时 {fmtDuration(item.summary.average_duration_ms)}</Text>
                <Text>Token {item.summary.total_tokens}</Text>
                <Text>工具 {item.summary.total_tool_calls}</Text>
              </Space>
            </List.Item>
          )}
        />
      )}

      <Divider orientation="left">已归档</Divider>
      {archives.length === 0 ? <Text type="secondary">暂无归档快照</Text> : (
        <Table
          size="small"
          rowKey="archive_id"
          pagination={{ pageSize: 5 }}
          dataSource={sortedArchives}
          columns={[
            { title: '完成数', key: 'completed', render: (_: unknown, r: EvalArchive) => `${r.summary.completed} / ${r.summary.total}` },
            { title: '通过数', key: 'passed', render: (_: unknown, r: EvalArchive) => `${r.summary.passed} / ${r.summary.total}` },
            { title: '版本', dataIndex: 'version', key: 'version' },
            { title: '评测集', dataIndex: 'suite', key: 'suite' },
            { title: '通过率', key: 'rate', render: (_: unknown, r: EvalArchive) => `${(r.summary.pass_rate * 100).toFixed(1)}%` },
            { title: '平均得分', key: 'score', render: (_: unknown, r: EvalArchive) => `${(r.summary.average_score * 100).toFixed(1)}%` },
            { title: '失败/超时/错误', key: 'issues', render: (_: unknown, r: EvalArchive) => `${r.summary.failed} / ${r.summary.timeout} / ${r.summary.errors}` },
            { title: '平均耗时', key: 'duration', render: (_: unknown, r: EvalArchive) => fmtDuration(r.summary.average_duration_ms) },
            { title: 'Token', key: 'tokens', render: (_: unknown, r: EvalArchive) => r.summary.total_tokens },
            { title: '工具调用', key: 'tool_calls', render: (_: unknown, r: EvalArchive) => r.summary.total_tool_calls },
            { title: '评测执行时间', dataIndex: 'started_at', key: 'started_at', render: fmtTime },
            { title: '归档 ID', dataIndex: 'archive_id', key: 'archive_id', ellipsis: true },
          ]}
          scroll={{ x: 1250 }}
        />
      )}
    </Modal>
  );
};

export default EvaluationCenter;

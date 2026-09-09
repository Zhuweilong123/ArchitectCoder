import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Checkbox, Col, Divider, Empty, Input, List, Modal, Popconfirm, Progress,
  Row, Select, Space, Statistic, Table, Tabs, Tag, Typography, message,
} from 'antd';
import {
  DeleteOutlined, EyeOutlined, FileAddOutlined, FileDoneOutlined, LineChartOutlined, PlayCircleOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { useUiStore } from '../../stores/uiStore';
import {
  archiveEvalBaseline, archiveEvalBatch, deleteEvalBatch, deleteEvalPerformanceResult, getEvalBatch, listEvalArchives, listEvalCases,
  getEvalBaseline, listEvalTrends, mergeEvalBatches, startEvalBatch,
  getEvalRepository,
  archiveEvalPerformanceResult, getEvalPerformanceResult, listEvalPerformanceResults,
  captureTraceCaseFixture, createTraceCaseDraft, deleteTraceCaseDraft, listTraceCaseDrafts, listTraceCaseProjects, previewTraceCaseFixture, publishTraceCaseDraft, reviewTraceCaseDraft, validateTraceCaseDraft, listTraces,
  type EvalArchive, type EvalBaseline, type EvalBatch, type EvalCaseInfo, type EvalPerformanceRun,
  type EvalRepositoryInfo, type EvalResult, type EvalTrend, type TraceCaseDraft, type TraceCaseFixturePreview, type TraceCaseProject, type TraceMeta,
} from '../../services/api';
import './EvaluationCenter.css';

const { Text } = Typography;
const EVAL_AGENT_LABEL = 'DevAgent';
const UNCLASSIFIED_SUITE = '__unclassified__';
const TRACE_SUITE = 'trace-3.1';
const ACTIVE_BATCH_STORAGE_KEY = 'evaluationActiveBatchId';
const COMPARISON_VERSION_COLORS = ['#1677ff', '#52c41a', '#faad14', '#722ed1', '#eb2f96', '#13c2c2'];

function fmtDuration(ms: number): string {
  if (!ms) return '-';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function fmtTime(value: string): string {
  if (!value) return '-';
  return new Date(value).toLocaleString();
}

function fileName(value: string): string {
  return value.split(/[\\/]/).pop() || value;
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

const FAILURE_CATEGORY_LABELS: Record<string, string> = {
  agent_failure: 'Agent',
  tool_failure: '工具',
  environment_failure: '环境',
  checker_failure: 'Checker',
  timeout: '超时',
  budget_exceeded: '预算',
};

function failureCategoryTag(category?: string): React.ReactNode {
  if (!category || category === 'none') return <Text type="secondary">-</Text>;
  return <Tag color="error">{FAILURE_CATEGORY_LABELS[category] || category}</Tag>;
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

type CheckerFieldKind = 'text' | 'list' | 'number' | 'json';
type CheckerFieldSpec = { key: string; label: string; kind?: CheckerFieldKind; required?: boolean; placeholder?: string };
type CheckerDefinition = { type: string; label: string; fields: CheckerFieldSpec[] };

const TRACE_CHECKER_DEFINITIONS: CheckerDefinition[] = [
  { type: 'file_exists', label: '文件存在', fields: [{ key: 'path', label: '路径', required: true }] },
  { type: 'file_absent', label: '文件不存在', fields: [{ key: 'path', label: '路径', required: true }] },
  { type: 'file_contains', label: '文件包含文本', fields: [{ key: 'path', label: '路径', required: true }, { key: 'text', label: '文本', required: true }] },
  { type: 'file_not_contains', label: '文件不包含文本', fields: [{ key: 'path', label: '路径', required: true }, { key: 'text', label: '文本', required: true }] },
  { type: 'json_field', label: 'JSON 字段', fields: [{ key: 'path', label: '路径', required: true }, { key: 'field', label: '字段路径', required: true }, { key: 'expected', label: '期望值', kind: 'json', required: true }] },
  { type: 'pytest', label: 'pytest', fields: [{ key: 'path', label: '测试路径', placeholder: '.' }, { key: 'args', label: '参数', kind: 'list' }, { key: 'timeout', label: '超时（秒）', kind: 'number' }] },
  { type: 'hidden_pytest', label: '隐藏 pytest', fields: [{ key: 'path', label: '测试路径', placeholder: '.' }, { key: 'args', label: '参数', kind: 'list' }, { key: 'timeout', label: '超时（秒）', kind: 'number' }] },
  { type: 'answer_contains_all', label: '回答包含全部内容', fields: [{ key: 'texts', label: '必含内容', kind: 'list', required: true }] },
  { type: 'answer_ordered_contains', label: '回答按顺序包含', fields: [{ key: 'texts', label: '有序内容', kind: 'list', required: true }] },
  { type: 'trace_policy', label: 'Trace 工具策略', fields: [{ key: 'max_tool_calls', label: '最大工具调用', kind: 'number' }, { key: 'required_tools', label: '必须使用工具', kind: 'list' }, { key: 'forbidden_tools', label: '禁止使用工具', kind: 'list' }] },
  { type: 'paths_unchanged', label: '路径保持不变', fields: [{ key: 'paths', label: '路径', kind: 'list', required: true }] },
  { type: 'uml_valid', label: 'UML 有效', fields: [{ key: 'path', label: '路径', required: true }, { key: 'diagram', label: '图表名称' }] },
  { type: 'uml_contains', label: 'UML 包含元素', fields: [{ key: 'path', label: '路径', required: true }, { key: 'kind', label: '元素类型', required: true }, { key: 'name', label: '元素名称', required: true }, { key: 'diagram', label: '图表名称' }] },
  { type: 'uml_absent', label: 'UML 不包含元素', fields: [{ key: 'path', label: '路径', required: true }, { key: 'kind', label: '元素类型', required: true }, { key: 'name', label: '元素名称', required: true }, { key: 'diagram', label: '图表名称' }, { key: 'class_name', label: '类名' }, { key: 'method', label: '方法名' }] },
  { type: 'uml_component_names', label: 'UML 组件名称', fields: [{ key: 'path', label: '路径', required: true }, { key: 'names', label: '组件名称', kind: 'list', required: true }, { key: 'diagram', label: '图表名称' }] },
  { type: 'uml_relation', label: 'UML 关系', fields: [{ key: 'path', label: '路径', required: true }, { key: 'source', label: '源元素', required: true }, { key: 'target', label: '目标元素', required: true }, { key: 'relation_type', label: '关系类型' }, { key: 'diagram', label: '图表名称' }] },
  { type: 'uml_method', label: 'UML 方法', fields: [{ key: 'path', label: '路径', required: true }, { key: 'class_name', label: '类名', required: true }, { key: 'method', label: '方法名', required: true }, { key: 'diagram', label: '图表名称' }] },
  { type: 'uml_method_signature', label: 'UML 方法签名', fields: [{ key: 'path', label: '路径', required: true }, { key: 'class_name', label: '类名', required: true }, { key: 'method', label: '方法名', required: true }, { key: 'params', label: '参数', kind: 'list', required: true }, { key: 'return_type', label: '返回类型' }, { key: 'diagram', label: '图表名称' }] },
  { type: 'uml_sequence', label: 'UML 时序包含', fields: [{ key: 'path', label: '路径', required: true }, { key: 'labels', label: '顺序标签', kind: 'list', required: true }, { key: 'diagram', label: '图表名称' }] },
  { type: 'uml_sequence_exact', label: 'UML 时序精确匹配', fields: [{ key: 'path', label: '路径', required: true }, { key: 'labels', label: '精确标签', kind: 'list', required: true }, { key: 'diagram', label: '图表名称' }] },
];

const checkerDefinitions = Object.fromEntries(TRACE_CHECKER_DEFINITIONS.map((definition) => [definition.type, definition]));

function validateCheckerConfigs(configs: Array<Record<string, any>>, scope: string): void {
  configs.forEach((config, index) => {
    const definition = checkerDefinitions[String(config.type || '')];
    if (!definition) throw new Error(`${scope}[${index}] 的 Checker 类型无效`);
    const missing = definition.fields.filter((field) => field.required && (config[field.key] === undefined || config[field.key] === '' || (Array.isArray(config[field.key]) && config[field.key].length === 0)));
    if (missing.length) throw new Error(`${scope}[${index}] 缺少：${missing.map((field) => field.label).join('、')}`);
  });
}

interface CheckerEditorProps {
  title: string;
  value: Array<Record<string, any>>;
  onChange: (value: Array<Record<string, any>>) => void;
}

const CheckerEditor: React.FC<CheckerEditorProps> = ({ title, value, onChange }) => {
  const updateChecker = (index: number, patch: Record<string, any>) => {
    onChange(value.map((checker, current) => current === index ? { ...checker, ...patch } : checker));
  };
  const updateField = (index: number, field: CheckerFieldSpec, rawValue: string) => {
    let parsed: any = rawValue;
    if (field.kind === 'list') parsed = rawValue.split(',').map((item) => item.trim()).filter(Boolean);
    if (field.kind === 'number') parsed = rawValue === '' ? undefined : Number(rawValue);
    if (field.kind === 'json' && rawValue.trim()) {
      try { parsed = JSON.parse(rawValue); } catch { parsed = rawValue; }
    }
    const next = { ...value[index] };
    if (parsed === undefined || parsed === '') delete next[field.key];
    else next[field.key] = parsed;
    updateChecker(index, next);
  };
  return (
    <Card size="small" title={`${title}（${value.length}）`} extra={<Button size="small" onClick={() => onChange([...value, { type: 'file_exists' }])}>添加 Checker</Button>}>
      <Space direction="vertical" style={{ width: '100%' }}>
        {value.length === 0 ? <Text type="secondary">暂无配置，点击右上角添加 Checker</Text> : value.map((checker, index) => {
          const definition = checkerDefinitions[String(checker.type || '')] || TRACE_CHECKER_DEFINITIONS[0];
          return (
            <Card key={`${index}-${checker.type || 'checker'}`} size="small" type="inner" title={`Checker ${index + 1}`} extra={<Button danger size="small" onClick={() => onChange(value.filter((_, current) => current !== index))}>删除</Button>}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Select
                  showSearch
                  optionFilterProp="label"
                  value={checker.type || undefined}
                  style={{ width: '100%' }}
                  placeholder="选择 Checker 类型"
                  options={TRACE_CHECKER_DEFINITIONS.map((item) => ({ value: item.type, label: `${item.label}（${item.type}）` }))}
                  onChange={(type) => onChange(value.map((item, current) => current === index ? { type } : item))}
                />
                <Space wrap style={{ width: '100%' }}>
                  {definition.fields.map((field) => {
                    const currentValue = checker[field.key];
                    const displayValue = field.kind === 'list' ? (Array.isArray(currentValue) ? currentValue.join(', ') : '') : field.kind === 'json' ? (currentValue === undefined ? '' : JSON.stringify(currentValue, null, 2)) : currentValue === undefined ? '' : String(currentValue);
                    const control = field.kind === 'json' ? (
                      <Input.TextArea value={displayValue} onChange={(event) => updateField(index, field, event.target.value)} autoSize={{ minRows: 1, maxRows: 4 }} placeholder={field.placeholder || 'JSON 值'} />
                    ) : <Input value={displayValue} onChange={(event) => updateField(index, field, event.target.value)} type={field.kind === 'number' ? 'number' : 'text'} placeholder={field.placeholder || (field.kind === 'list' ? '多个值用逗号分隔' : '')} />;
                    return <div key={field.key} style={{ minWidth: 220, flex: '1 1 220px' }}><Text type={field.required ? undefined : 'secondary'}>{field.label}{field.required ? ' *' : '（可选）'}</Text>{control}</div>;
                  })}
                </Space>
              </Space>
            </Card>
          );
        })}
      </Space>
    </Card>
  );
};
const EvaluationCenter: React.FC = () => {
  const {
    clearTraceCaseFactoryRequest, evaluationVisible, traceCaseFactoryRequestedSessionId, setEvaluationVisible, setTraceSessionId, setTraceVisible,
  } = useUiStore();
  const [cases, setCases] = useState<EvalCaseInfo[]>([]);
  const [baseline, setBaseline] = useState<EvalBaseline | null>(null);
  const [repository, setRepository] = useState<EvalRepositoryInfo | null>(null);
  const [trends, setTrends] = useState<EvalTrend[]>([]);
  const [archives, setArchives] = useState<EvalArchive[]>([]);
  const [performanceRuns, setPerformanceRuns] = useState<EvalPerformanceRun[]>([]);
  const [performanceLoading, setPerformanceLoading] = useState(false);
  const [performanceArchiving, setPerformanceArchiving] = useState(false);
  const [deletingPerformanceId, setDeletingPerformanceId] = useState<string | null>(null);
  const [selectedPerformance, setSelectedPerformance] = useState<EvalPerformanceRun | null>(null);
  const [performanceVersion, setPerformanceVersion] = useState('');
  const [performanceQuery, setPerformanceQuery] = useState('');
  const [performanceArchiveFilter, setPerformanceArchiveFilter] = useState<'all' | 'archived' | 'pending'>('all');
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [resultQuery, setResultQuery] = useState('');
  const [resultStatus, setResultStatus] = useState<'all' | 'passed' | 'failed' | 'timeout'>('all');
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [selectedTrendBatch, setSelectedTrendBatch] = useState<EvalBatch | null>(null);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [mergingRuns, setMergingRuns] = useState(false);
  const [deletingRunId, setDeletingRunId] = useState<string | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);
  const [archiveQuery, setArchiveQuery] = useState('');
  const [activeTab, setActiveTab] = useState<EvaluationTab>('overview');
  const [selectedSuites, setSelectedSuites] = useState<string[]>([]);
  const [batch, setBatch] = useState<EvalBatch | null>(null);
  const [loading, setLoading] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [traceCaseVisible, setTraceCaseVisible] = useState(false);
  const [traceCaseLoading, setTraceCaseLoading] = useState(false);
  const [traceCaseActionLoading, setTraceCaseActionLoading] = useState(false);
  const [traceCaseTraces, setTraceCaseTraces] = useState<TraceMeta[]>([]);
  const [traceCaseDrafts, setTraceCaseDrafts] = useState<TraceCaseDraft[]>([]);
  const [traceCaseProjects, setTraceCaseProjects] = useState<TraceCaseProject[]>([]);
  const [traceCaseSessionId, setTraceCaseSessionId] = useState('');
  const [traceCaseProjectId, setTraceCaseProjectId] = useState('');
  const [traceCaseName, setTraceCaseName] = useState('');
  const [traceCaseReviewName, setTraceCaseReviewName] = useState('');
  const [traceCaseReviewProjectId, setTraceCaseReviewProjectId] = useState('');
  const [traceCaseCheckers, setTraceCaseCheckers] = useState<Array<Record<string, any>>>([]);
  const [traceCaseHardCheckers, setTraceCaseHardCheckers] = useState<Array<Record<string, any>>>([]);
  const [traceCaseDraft, setTraceCaseDraft] = useState<TraceCaseDraft | null>(null);
  const [traceCaseFixturePreview, setTraceCaseFixturePreview] = useState<TraceCaseFixturePreview | null>(null);
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
  const baselineCases = useMemo(() => cases.filter((item) => (
    (item.metadata?.suite ? String(item.metadata.suite) : UNCLASSIFIED_SUITE) !== TRACE_SUITE
  )), [cases]);
  const baselineCaseIds = useMemo(() => baselineCases.map((item) => item.id), [baselineCases]);
  const baselineSnapshotMatchesCatalog = !!baseline
    && Array.isArray(baseline.case_ids)
    && baseline.case_ids.length === baselineCaseIds.length
    && baselineCaseIds.every((caseId) => baseline.case_ids?.includes(caseId));
  const baselineBatchIdsMatch = (caseIds: string[]) => (
    caseIds.length === baselineCaseIds.length
    && baselineCaseIds.every((caseId) => caseIds.includes(caseId))
  );
  const archiveBatchReady = !!batch
    && batch.status === 'completed'
    && cases.length > 0
    && baselineBatchIdsMatch(batch.case_ids);
  const archiveBaselineReady = !batch && baselineSnapshotMatchesCatalog;
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
      || selectedTrendBatch?.results?.find((result) => result.case_id === selectedCaseId)
      || batch?.results?.find((result) => result.case_id === selectedCaseId)
      || null,
    [batch, filteredSelectedResults, selectedCaseId, selectedPerformance, selectedTrendBatch],
  );

  const catalogGroups = useMemo(() => {
    const grouped = new Map<string, number>();
    baselineCases.forEach((item) => {
      const suite = item.metadata?.suite ? String(item.metadata.suite) : UNCLASSIFIED_SUITE;
      grouped.set(suite, (grouped.get(suite) || 0) + 1);
    });
    return Array.from(grouped.entries()).map(([name, total]) => ({ name, total }));
  }, [baselineCases]);

  const baselineIsCurrent = baselineSnapshotMatchesCatalog;

  const comparisonRuns = useMemo(
    () => performanceRuns
      .filter((run) => comparisonIds.includes(run.result_id))
      .sort((left, right) => {
        const leftTime = Date.parse(left.started_at);
        const rightTime = Date.parse(right.started_at);
        const safeLeftTime = Number.isNaN(leftTime) ? 0 : leftTime;
        const safeRightTime = Number.isNaN(rightTime) ? 0 : rightTime;
        return safeLeftTime - safeRightTime || left.result_id.localeCompare(right.result_id);
      }),
    [comparisonIds, performanceRuns],
  );

  const selectedRuns = useMemo(
    () => trends.filter((item) => selectedRunIds.includes(item.batch_id)),
    [selectedRunIds, trends],
  );
  const selectedRunVersionsMatch = selectedRuns.length > 0
    && selectedRuns.every((item) => item.version === selectedRuns[0].version);

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
      const storedBatchId = window.localStorage.getItem(ACTIVE_BATCH_STORAGE_KEY);
      const latestBatchId = storedBatchId || trendList[0]?.batch_id;
      if (latestBatchId) {
        try {
          setBatch(await getEvalBatch(latestBatchId));
        } catch {
          window.localStorage.removeItem(ACTIVE_BATCH_STORAGE_KEY);
          setBatch(null);
        }
      } else {
        setBatch(null);
      }
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
      if (error?.response?.status === 404) {
        setSelectedPerformance(null);
        setComparisonIds((current) => current.filter((id) => id !== run.result_id));
        setPerformanceRuns(await listEvalPerformanceResults());
        message.info('该性能结果已被删除，列表已刷新');
        return;
      }
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

  const deletePerformance = async (run: EvalPerformanceRun) => {
    setDeletingPerformanceId(run.result_id);
    try {
      await deleteEvalPerformanceResult(run.result_id);
      if (selectedPerformance?.result_id === run.result_id) {
        setSelectedPerformance(null);
        setSelectedCaseId(null);
      }
      await refresh();
      message.success('性能结果本地数据已删除');
    } catch (error: any) {
      message.error(`删除性能结果失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setDeletingPerformanceId(null);
    }
  };

  const deleteRun = async (item: EvalTrend) => {
    setDeletingRunId(item.batch_id);
    try {
      await deleteEvalBatch(item.batch_id);
      setSelectedRunIds((current) => current.filter((id) => id !== item.batch_id));
      if (selectedTrendBatch?.batch_id === item.batch_id) {
        setSelectedTrendBatch(null);
        setSelectedCaseId(null);
      }
      if (batch?.batch_id === item.batch_id) {
        setBatch(null);
        window.localStorage.removeItem(ACTIVE_BATCH_STORAGE_KEY);
      }
      await refresh();
      message.success('运行批次本地数据已删除');
    } catch (error: any) {
      message.error(`删除运行批次失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setDeletingRunId(null);
    }
  };

  const restoreTraceCaseDraft = (draft: TraceCaseDraft) => {
    setTraceCaseDraft(draft);
    setTraceCaseFixturePreview(null);
    setTraceCaseReviewName(draft.case.name);
    setTraceCaseReviewProjectId(draft.case.project_id);
    setTraceCaseCheckers(draft.case.checkers || []);
    setTraceCaseHardCheckers(draft.case.hard_checkers || []);
  };

  const removeTraceCaseDraft = async (draft: TraceCaseDraft) => {
    try {
      await deleteTraceCaseDraft(draft.draft_id);
      setTraceCaseDrafts((current) => current.filter((item) => item.draft_id !== draft.draft_id));
      if (traceCaseDraft?.draft_id === draft.draft_id) { setTraceCaseDraft(null); setTraceCaseFixturePreview(null); }
      message.success('评测用例草稿已删除');
    } catch (error: any) {
      message.error(`删除草稿失败：${error?.response?.data?.detail || error.message || error}`);
    }
  };
  const openTraceCaseFactory = async (requestedSessionId = "") => {
    setTraceCaseVisible(true);
    setTraceCaseDraft(null);
    setTraceCaseFixturePreview(null);
    setTraceCaseLoading(true);
    try {
      const [traces, projects, drafts] = await Promise.all([listTraces(), listTraceCaseProjects(), listTraceCaseDrafts()]);
      setTraceCaseTraces(traces);
      setTraceCaseDrafts(drafts);
      setTraceCaseProjects(projects);
      const preferredSessionId = requestedSessionId && traces.some((trace) => trace.session_id === requestedSessionId)
        ? requestedSessionId
        : traceCaseSessionId && traces.some((trace) => trace.session_id === traceCaseSessionId)
          ? traceCaseSessionId
          : traces[0]?.session_id || '';
      setTraceCaseSessionId(preferredSessionId);
      setTraceCaseProjectId((current) => current || projects[0]?.id || '');
    } catch (error: any) {
      message.error(`Trace 用例能力加载失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setTraceCaseLoading(false);
    }
  };

  useEffect(() => {
    if (!evaluationVisible || !traceCaseFactoryRequestedSessionId) return;
    const requestedSessionId = traceCaseFactoryRequestedSessionId;
    clearTraceCaseFactoryRequest();
    void openTraceCaseFactory(requestedSessionId);
    // The request is a one-shot cross-panel navigation signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evaluationVisible, traceCaseFactoryRequestedSessionId]);
  const createTraceCase = async () => {
    if (!traceCaseSessionId) {
      message.warning('请先选择一个 Trace 会话');
      return;
    }
    setTraceCaseActionLoading(true);
    try {
      const draft = await createTraceCaseDraft({
        session_id: traceCaseSessionId,
        name: traceCaseName.trim(),
        project_id: traceCaseProjectId,
        suite: 'trace-derived',
        include_trace_policy: true,
      });
      restoreTraceCaseDraft(draft);
      setTraceCaseDrafts((current) => [draft, ...current.filter((item) => item.draft_id !== draft.draft_id)]);
      message.success('Trace 已转换为评测用例草稿');
    } catch (error: any) {
      message.error(`Trace 转换失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setTraceCaseActionLoading(false);
    }
  };

  const previewTraceCase = async () => {
    if (!traceCaseDraft) return;
    setTraceCaseActionLoading(true);
    try {
      const preview = await previewTraceCaseFixture(traceCaseDraft.draft_id);
      setTraceCaseFixturePreview(preview);
    } catch (error: any) {
      message.error(`fixture 预览失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setTraceCaseActionLoading(false);
    }
  };

  const captureTraceCase = async () => {
    if (!traceCaseDraft || !traceCaseFixturePreview) return;
    setTraceCaseActionLoading(true);
    try {
      const draft = await captureTraceCaseFixture(traceCaseDraft.draft_id);
      restoreTraceCaseDraft(draft);
      setTraceCaseDrafts((current) => current.map((item) => item.draft_id === draft.draft_id ? draft : item));
      message.success(`工作区已捕获为 fixture（${draft.capture?.file_count || 0} 个文件）`);
    } catch (error: any) {
      message.error(`fixture 捕获失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setTraceCaseActionLoading(false);
    }
  };
  const addTraceCaseCandidate = (candidate: Record<string, any>, target: 'diagnostic' | 'hard') => {
    const setter = target === 'hard' ? setTraceCaseHardCheckers : setTraceCaseCheckers;
    setter((current) => {
      const exists = current.some((item) => item.type === candidate.type && JSON.stringify(item) === JSON.stringify(candidate));
      if (exists) return current;
      return [...current, { ...candidate }];
    });
    message.success(target === 'hard' ? '候选约束已加入 Hard Checkers' : '候选约束已加入 Diagnostic Checkers');
  };
  const saveTraceCaseReview = async () => {
    if (!traceCaseDraft) return;
    try {
      validateCheckerConfigs(traceCaseCheckers, 'checkers');
      validateCheckerConfigs(traceCaseHardCheckers, 'hard_checkers');
    } catch (error: any) {
      message.error(`Checker 配置无效：${error.message || error}`);
      return;
    }
    setTraceCaseActionLoading(true);
    try {
      const draft = await reviewTraceCaseDraft(traceCaseDraft.draft_id, {
        name: traceCaseReviewName.trim(),
        project_id: traceCaseReviewProjectId,
        checkers: traceCaseCheckers,
        hard_checkers: traceCaseHardCheckers,
      });
      setTraceCaseDraft(draft);
      setTraceCaseFixturePreview(null);
      message.success('草稿审核内容已保存，请重新执行隔离试运行');
    } catch (error: any) {
      message.error(`保存审核内容失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setTraceCaseActionLoading(false);
    }
  };
  const validateTraceCase = async () => {
    if (!traceCaseDraft) return;
    setTraceCaseActionLoading(true);
    try {
      const draft = await validateTraceCaseDraft(traceCaseDraft.draft_id);
      setTraceCaseDraft(draft);
      message[draft.status === 'validated' ? 'success' : 'error'](
        draft.status === 'validated' ? '隔离试运行通过，可以发布' : '隔离试运行未通过，请检查结果',
      );
    } catch (error: any) {
      message.error(`隔离试运行失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setTraceCaseActionLoading(false);
    }
  };

  const publishTraceCase = async () => {
    if (!traceCaseDraft) return;
    setTraceCaseActionLoading(true);
    try {
      await publishTraceCaseDraft(traceCaseDraft.draft_id);
      await refresh();
      setTraceCaseDraft({ ...traceCaseDraft, status: 'published' });
      message.success('评测用例已发布到本地目录');
    } catch (error: any) {
      message.error(`发布评测用例失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setTraceCaseActionLoading(false);
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
        window.localStorage.setItem(ACTIVE_BATCH_STORAGE_KEY, batchId);
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
      window.localStorage.setItem(ACTIVE_BATCH_STORAGE_KEY, next.batch_id);
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
    { title: '失败归因', dataIndex: 'failure_category', key: 'failure_category', width: 95, render: failureCategoryTag },
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
  const renderLegacyBaseline = () => baseline ? (
    <Card size="small" className="evaluation-baseline-card" title={<Space><LineChartOutlined />性能基线</Space>} extra={<Space><Tag color="blue">{EVAL_AGENT_LABEL}</Tag><Text type="secondary">{baseline.version}</Text></Space>}>
      <div className="evaluation-baseline-scope">
        <Tag color="green">正式基线 {baselineCaseIds.length} 个用例</Tag>
        {cases.length !== baselineCaseIds.length ? <Tag>当前目录 {cases.length} 个用例（含诊断集）</Tag> : null}
      </div>
      <div className="evaluation-baseline-meta">{baseline.label} · {baseline.model} · 快照时间：{fmtTime(baseline.captured_at)}</div>
      <Row gutter={[12, 12]} className="evaluation-stat-row">
        <Col xs={12} sm={8} md={4}><Statistic title="用例数" value={baseline.case_count} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="通过率" value={baseline.pass_rate} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="平均得分" value={baseline.average_score} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="通过 / 失败 / 超时 / 错误" value={`${baseline.passed} / ${baseline.failed} / ${baseline.timeout} / ${baseline.errors ?? 0}`} /></Col>
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

  const renderBaseline = () => {
    if (!baseline || baselineIsCurrent) return renderLegacyBaseline();
    return (
      <Card size="small" className="evaluation-baseline-card" title={<Space><LineChartOutlined />性能基线</Space>} extra={<Tag color="warning">目录已更新，基线待重建</Tag>}>
        <Alert
          type="warning"
          showIcon
          message="当前基线仍对应旧评测目录"
          description={`当前目录有 ${cases.length} 个用例，旧基线快照仅覆盖 ${baseline.case_count} 个用例。旧基线指标不再展示为当前成绩，请完成全量评测后重新归档。`}
          style={{ marginBottom: 12 }}
        />
        <Table
          size="small"
          rowKey="name"
          pagination={false}
          columns={[
            { title: '当前评测范围', dataIndex: 'name', key: 'name' },
            { title: '用例数', dataIndex: 'total', key: 'total' },
            { title: '基线状态', key: 'status', render: () => <Tag color="warning">待重建</Tag> },
          ]}
          dataSource={catalogGroups}
        />
      </Card>
    );
  };

  const renderBatch = () => batch ? (
    <Card size="small" title="当前评测批次">
      <div className="evaluation-batch-line"><Text strong>{batch.version}</Text> · {batch.suite} · {statusTag(batch.status)}{batch.current_case_id ? <Text type="secondary">当前：{batch.current_case_id}</Text> : null}<Text type="secondary">开始：{fmtTime(batch.started_at)}</Text></div>
      {batch.summary.total > 0 ? (() => {
        const active = batch.status === 'running' || batch.status === 'queued';
        const completed = !active && batch.status === 'completed'
          ? batch.summary.total
          : batch.summary.completed;
        return <Progress percent={Math.min(100, Math.round(completed / batch.summary.total * 100))} status={active ? 'active' : batch.status === 'completed' ? 'success' : 'exception'} />;
      })() : null}
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
    { title: '版本', dataIndex: 'version', key: 'version', width: 220, render: (v: string) => v ? <Text className="evaluation-version-cell" ellipsis={{ tooltip: v }} title={v}>{v}</Text> : <Text type="secondary">未标记</Text> },
    { title: '结果文件', dataIndex: 'source_path', key: 'source_path', width: 220, render: (v: string, row: EvalPerformanceRun) => {
      const value = v || row.file_name || '-';
      return <Text className="evaluation-file-cell" ellipsis={{ tooltip: value }} title={value}>{fileName(value)}</Text>;
    } },
    { title: '执行时间', dataIndex: 'started_at', key: 'started_at', width: 150, render: fmtTime },
    { title: '用例数', dataIndex: 'result_count', key: 'result_count', width: 75 },
    { title: '通过率', key: 'pass_rate', width: 90, render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.pass_rate * 100).toFixed(1)}%` },
    { title: '平均得分', key: 'score', width: 100, render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.average_score * 100).toFixed(1)}%` },
    { title: '平均耗时', key: 'duration', width: 100, render: (_: unknown, row: EvalPerformanceRun) => fmtDuration(row.summary.average_duration_ms) },
    { title: 'Token', key: 'tokens', width: 110, render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tokens },
    { title: '归档', key: 'archived', width: 90, render: (_: unknown, row: EvalPerformanceRun) => row.archived ? <Tag color="success">已归档</Tag> : <Tag>未归档</Tag> },
      { title: '操作', key: 'action', width: 170, render: (_: unknown, row: EvalPerformanceRun) => (
        <Space>
          <Button size="small" onClick={(event) => { event.stopPropagation(); selectPerformance(row); }}>查看</Button>
          <Popconfirm
            title="删除本地性能结果？"
            description="将删除本地性能 JSONL 数据，已归档快照不受影响。"
            okText="确认删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => deletePerformance(row)}
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={deletingPerformanceId === row.result_id}
              onClick={(event) => event.stopPropagation()}
              onMouseDown={(event) => event.stopPropagation()}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ) },
  ];

  const renderFailureDetail = () => selectedCase ? (
    <Card size="small" className="evaluation-case-detail" title={<Space>用例钻取：{selectedCase.case_id}{statusTag(selectedCase.status, selectedCase.passed)}</Space>}>
      {selectedCase.failure_category && selectedCase.failure_category !== 'none' ? <div>失败归因：{failureCategoryTag(selectedCase.failure_category)}</div> : null}
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
      <Table size="small" rowKey="result_id" loading={performanceLoading} dataSource={filteredPerformanceRuns} pagination={{ pageSize: 6 }} scroll={{ x: 1180 }} rowSelection={{ selectedRowKeys: comparisonIds, onChange: (keys) => setComparisonIds(keys as string[]) }} rowClassName={(row) => selectedPerformance?.result_id === row.result_id ? 'evaluation-selected-row' : ''} onRow={(row) => ({ onClick: (event) => { const target = event.target as HTMLElement; if (target.closest('button, a, [role="button"]')) return; selectPerformance(row); } })} columns={performanceColumns} />
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
        <>
          <Table size="small" rowKey="result_id" pagination={false} dataSource={comparisonRuns} columns={[
            { title: '版本', dataIndex: 'version', key: 'version', render: (v: string, row: EvalPerformanceRun) => v || row.file_name },
            { title: '执行时间', dataIndex: 'started_at', key: 'started_at', render: fmtTime },
            { title: '用例数', dataIndex: 'result_count', key: 'result_count' },
            { title: '通过率', key: 'pass_rate', render: (_: unknown, row: EvalPerformanceRun) => (row.summary.pass_rate * 100).toFixed(1) + '%' },
            { title: '平均得分', key: 'score', render: (_: unknown, row: EvalPerformanceRun) => (row.summary.average_score * 100).toFixed(1) + '%' },
            { title: '平均耗时', key: 'duration', render: (_: unknown, row: EvalPerformanceRun) => fmtDuration(row.summary.average_duration_ms) },
            { title: 'Token', key: 'tokens', render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tokens },
            { title: '工具调用', key: 'tools', render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tool_calls },
          ]} />
          <div className="evaluation-comparison-chart">
            <div className="evaluation-comparison-chart-header">
              <Text strong>选中版本性能对比</Text>
              <Text type="secondary">按构建时间从左到右排列，相邻版本标注数值变化</Text>
            </div>
            <div className="evaluation-comparison-chart-legend" aria-label="版本颜色图例">
              {comparisonRuns.map((run, index) => {
                const version = run.version || run.file_name;
                const versionIndex = comparisonRuns.findIndex((candidate) => (candidate.version || candidate.file_name) === version);
                const color = COMPARISON_VERSION_COLORS[versionIndex % COMPARISON_VERSION_COLORS.length];
                return (
                  <span className="evaluation-comparison-chart-legend-item" key={run.result_id}>
                    <span className="evaluation-comparison-chart-legend-dot" style={{ backgroundColor: color }} />
                    <Text ellipsis={{ tooltip: version }}>{version}</Text>
                  </span>
                );
              })}
            </div>
            <div className="evaluation-comparison-chart-grid">
              {[
                { key: 'pass-rate', label: '通过率', value: (run: EvalPerformanceRun) => run.summary.pass_rate * 100, format: (value: number) => value.toFixed(1) + '%', max: 100, higherIsBetter: true },
                { key: 'score', label: '平均得分', value: (run: EvalPerformanceRun) => run.summary.average_score * 100, format: (value: number) => value.toFixed(1) + '%', max: 100, higherIsBetter: true },
                { key: 'duration', label: '平均耗时', value: (run: EvalPerformanceRun) => run.summary.average_duration_ms, format: (value: number) => fmtDuration(value), higherIsBetter: false },
                { key: 'tokens', label: 'Token', value: (run: EvalPerformanceRun) => run.summary.total_tokens, format: (value: number) => value.toLocaleString(), higherIsBetter: false },
                { key: 'tools', label: '工具调用', value: (run: EvalPerformanceRun) => run.summary.total_tool_calls, format: (value: number) => value.toLocaleString(), higherIsBetter: false },
              ].map((metric) => {
                const maxValue = metric.max || Math.max(...comparisonRuns.map(metric.value), 1);
                return (
                  <div className="evaluation-chart-metric" key={metric.key}>
                    <div className="evaluation-chart-metric-title">{metric.label}</div>
                    <div className="evaluation-chart-bars" role="img" aria-label={metric.label + '版本对比'}>
                      {comparisonRuns.map((run, index) => {
                        const value = metric.value(run);
                        const previousValue = index > 0 ? metric.value(comparisonRuns[index - 1]) : null;
                        const change = previousValue === null || previousValue === 0
                          ? null
                          : ((value - previousValue) / Math.abs(previousValue)) * 100;
                        const slope = change === null || change === 0 ? 'flat' : change > 0 ? 'up' : 'down';
                        const sentiment = change === null || change === 0 ? 'flat' : metric.higherIsBetter === (change > 0) ? 'positive' : 'negative';
                        const height = value > 0 ? Math.max((value / maxValue) * 100, 4) : 0;
                        const version = run.version || run.file_name;
                        const versionIndex = comparisonRuns.findIndex((candidate) => (candidate.version || candidate.file_name) === version);
                        const color = COMPARISON_VERSION_COLORS[versionIndex % COMPARISON_VERSION_COLORS.length];
                        return (
                          <React.Fragment key={run.result_id}>
                            {index > 0 ? (
                              <div className={'evaluation-chart-change evaluation-chart-change-slope-' + slope + ' evaluation-chart-change-' + sentiment} aria-label={'较上一版本变化 ' + (change === null ? '不可计算' : (change > 0 ? '+' : '') + change.toFixed(1) + '%')}>
                                <span className="evaluation-chart-change-line" />
                                <span className="evaluation-chart-change-label">{change === null ? '—' : (change > 0 ? '+' : '') + change.toFixed(1) + '%'}</span>
                              </div>
                            ) : null}
                            <div className="evaluation-chart-bar-item" title={version + ': ' + metric.format(value)}>
                              <div className="evaluation-chart-bar-value">{metric.format(value)}</div>
                              <div className="evaluation-chart-bar-track">
                                <div className="evaluation-chart-bar" style={{ height: String(Math.min(height, 100)) + '%', backgroundColor: color }} />
                              </div>
                              <Text ellipsis={{ tooltip: version }} className="evaluation-chart-bar-label">{version}</Text>
                            </div>
                          </React.Fragment>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </>
  );
  const renderRuns = () => trends.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无批次记录" /> : (
    <List size="small" dataSource={trends} renderItem={(item) => (
      <List.Item><List.Item.Meta title={<Space>{item.version}{statusTag(item.status)}<Text type="secondary">{item.suite}</Text></Space>} description={fmtTime(item.started_at)} /><Space className="evaluation-trend-values"><Text>通过率 {(item.summary.pass_rate * 100).toFixed(1)}%</Text><Text>得分 {(item.summary.average_score * 100).toFixed(1)}%</Text><Text>完成 {item.summary.completed}/{item.summary.total}</Text><Text>失败 {item.summary.failed}</Text><Text>超时 {item.summary.timeout}</Text><Text>耗时 {fmtDuration(item.summary.average_duration_ms)}</Text><Text>Token {item.summary.total_tokens}</Text><Text>工具 {item.summary.total_tool_calls}</Text></Space></List.Item>
    )} />
  );

  const openTrend = async (item: EvalTrend) => {
    if (selectedTrendBatch?.batch_id === item.batch_id) {
      setSelectedTrendBatch(null);
      setSelectedCaseId(null);
      return;
    }
    setTrendLoading(true);
    setSelectedCaseId(null);
    try {
      setSelectedTrendBatch(await getEvalBatch(item.batch_id));
    } catch (error: any) {
      if (error?.response?.status === 404) {
        setSelectedTrendBatch(null);
        setSelectedRunIds((current) => current.filter((id) => id !== item.batch_id));
        setTrends((current) => current.filter((trend) => trend.batch_id !== item.batch_id));
        message.info('该运行批次已被删除，列表已刷新');
        return;
      }
      message.error(`历史批次详情加载失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setTrendLoading(false);
    }
  };

  const mergeSelectedRuns = async () => {
    if (selectedRuns.length < 2) return;
    setMergingRuns(true);
    try {
      const version = selectedRuns[0].version || repository?.version || 'working-tree';
      const merged = await mergeEvalBatches({
        batch_ids: selectedRuns.map((item) => item.batch_id),
        version,
        label: `${version} merged performance`,
      });
      setSelectedRunIds([]);
      message.success(`已合并 ${merged.results.length} 个用例，并登记为性能结果`);
      await refresh();
      setActiveTab('performance');
    } catch (error: any) {
      message.error(`批次合并失败：${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setMergingRuns(false);
    }
  };

  const renderRunsWithDetails = () => trends.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无批次记录" /> : (
    <>
      <Space wrap className="evaluation-filter-row">
        <Text type="secondary">已选 {selectedRuns.length} 个运行批次</Text>
        <Button
          type="primary"
          disabled={selectedRuns.length < 2 || !selectedRunVersionsMatch || selectedRuns.some((item) => item.status !== 'completed')}
          loading={mergingRuns}
          onClick={mergeSelectedRuns}
        >
          合并为性能结果
        </Button>
      </Space>
      <List size="small" dataSource={trends} renderItem={(item) => (
      <React.Fragment key={item.batch_id}>
        <List.Item
          className="evaluation-trend-item"
          actions={[
            <Button type="link" loading={trendLoading && selectedTrendBatch?.batch_id === item.batch_id} onClick={(event) => { event.stopPropagation(); openTrend(item); }}>{selectedTrendBatch?.batch_id === item.batch_id ? '收起详情' : '查看详情'}</Button>,
            <Popconfirm
              title="删除本地运行批次？"
              description="将删除本地批次记录及其运行数据，不会修改代码仓或基线文件。"
              okText="确认删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => deleteRun(item)}
            >
              <Button
                type="link"
                danger
                icon={<DeleteOutlined />}
                disabled={item.status === 'running' || item.status === 'queued'}
                loading={deletingRunId === item.batch_id}
                onClick={(event) => event.stopPropagation()}
                onMouseDown={(event) => event.stopPropagation()}
              >
                删除
              </Button>
            </Popconfirm>,
          ]}
          onClick={(event) => { const target = event.target as HTMLElement; if (target.closest('button, a, input, label, [role="button"]')) return; openTrend(item); }}
        >
          <div className="evaluation-run-main">
          <Checkbox
            checked={selectedRunIds.includes(item.batch_id)}
            disabled={item.status !== 'completed'}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => setSelectedRunIds((current) => event.target.checked
              ? [...current, item.batch_id]
              : current.filter((id) => id !== item.batch_id))}
            style={{ marginRight: 12 }}
          />
          <div className="evaluation-run-copy">
            <List.Item.Meta
              title={<Space className="evaluation-run-heading"><Text className="evaluation-version-cell" ellipsis={{ tooltip: item.version }} title={item.version}>{item.version}</Text>{statusTag(item.status)}<Text type="secondary">{item.suite}</Text></Space>}
              description={fmtTime(item.started_at)}
            />
            <Space className="evaluation-trend-values"><Text>通过率 {(item.summary.pass_rate * 100).toFixed(1)}%</Text><Text>得分 {(item.summary.average_score * 100).toFixed(1)}%</Text><Text>完成 {item.summary.completed}/{item.summary.total}</Text><Text>失败 {item.summary.failed}</Text><Text>超时 {item.summary.timeout}</Text><Text>耗时 {fmtDuration(item.summary.average_duration_ms)}</Text><Text>Token {item.summary.total_tokens}</Text><Text>工具 {item.summary.total_tool_calls}</Text></Space>
          </div>
          </div>
        </List.Item>
        {selectedTrendBatch?.batch_id === item.batch_id ? (
          <List.Item>
            <Card size="small" title={`批次详情 · ${item.version} · ${item.suite}`} style={{ width: '100%' }}>
              <Table size="small" rowKey="case_id" pagination={{ pageSize: 8 }} columns={resultColumns} dataSource={selectedTrendBatch.results} onRow={(row) => ({ onClick: () => setSelectedCaseId(row.case_id) })} />
              {renderFailureDetail()}
            </Card>
          </List.Item>
        ) : null}
      </React.Fragment>
      )} />
    </>
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
    <Modal className="evaluation-center-modal" title={<Space><LineChartOutlined />ArchitectCoder 能力基准中心</Space>} open={evaluationVisible} onCancel={() => setEvaluationVisible(false)} footer={null} width={1220} styles={{ body: { maxHeight: 'calc(100vh - 150px)', overflowY: 'auto' } }}>
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
          <Tag color="green">正式基线 {baselineCaseIds.length} 个</Tag>
          <Tag color={selectedCaseIds.length === cases.length && cases.length > 0 ? 'green' : 'blue'}>当前选择 {selectedCaseIds.length} / {cases.length || '-'} 个</Tag>
          <Tag color={repository?.dirty ? 'warning' : 'green'} title={repository?.commit || undefined}>{repository ? `${repository.branch}@${repository.commit.slice(0, 12)}${repository.dirty ? ' · dirty' : ''}` : '读取仓库版本中…'}</Tag>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={runBatch} loading={loading} disabled={!selectedCaseIds.length || !repository?.version || repository.version === 'unknown' || !!batch && ['running', 'queued'].includes(batch.status)}>一键运行</Button>
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>刷新</Button>
          <Button icon={<FileAddOutlined />} onClick={() => openTraceCaseFactory()}>Trace 转用例</Button>
          <Button icon={<EyeOutlined />} onClick={openPerformance}>性能结果</Button>
          <Popconfirm title="确认归档当前完整评测？" description="只有全部评测集执行完成后才允许归档，原始结果不会被覆盖。" okText="确认归档" cancelText="取消" onConfirm={archiveBatch}><Button icon={<FileDoneOutlined />} loading={archiving} disabled={!archiveReady}>一键归档</Button></Popconfirm>
        </Space>
        <div className="evaluation-hint">支持组合评测集运行；“全选”会执行当前目录下全部评测用例。只有全部用例完成后才允许一键归档。版本自动取当前 Git 分支和 HEAD commit。</div>
      </Card>
      <Tabs activeKey={activeTab} onChange={(key) => setActiveTab(key as EvaluationTab)} items={[
        { key: 'overview', label: '概览与当前批次', children: <>{renderBaseline()}<Divider orientation="left">当前批次</Divider>{renderBatch()}</> },
        { key: 'runs', label: '运行批次', children: renderRunsWithDetails() },
        { key: 'performance', label: `性能结果 (${performanceRuns.length})`, children: renderPerformance() },
        { key: 'comparison', label: `多版本对比${comparisonRuns.length ? ` (${comparisonRuns.length})` : ''}`, children: renderComparison() },
        { key: 'archives', label: `已归档 (${archives.length})`, children: renderArchives() },
      ]} />
      <Modal
        title="Trace 一键转评测用例"
        open={traceCaseVisible}
        onCancel={() => setTraceCaseVisible(false)}
        footer={null}
        width={760}
      >
        {traceCaseLoading ? <div style={{ padding: 24, textAlign: 'center' }}>加载 Trace 与评测项目中…</div> : traceCaseDraft ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space wrap>
              <Tag color={traceCaseDraft.status === 'validated' ? 'green' : traceCaseDraft.status === 'published' ? 'blue' : 'orange'}>
                {traceCaseDraft.status}
              </Tag>
              <Text type="secondary">会话：{traceCaseDraft.session_id}</Text>
              <Text type="secondary">事件：{traceCaseDraft.trace_summary.events || 0}</Text>
              <Text type="secondary">工具调用：{traceCaseDraft.trace_summary.tool_calls || 0}</Text>
            </Space>
            {traceCaseDraft.warnings.map((warning) => <Alert key={warning} type="warning" showIcon message={warning} />)}
            <Card size="small" title={traceCaseDraft.case.name}>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>{traceCaseDraft.case.prompt || traceCaseDraft.case.turns?.map((turn) => turn.prompt).join('\n\n')}</Typography.Paragraph>
              <Text type="secondary">项目：{traceCaseDraft.case.project_id || '未绑定'}；候选约束：{traceCaseDraft.candidate_checkers.length} 个</Text>
            </Card>
            {traceCaseDraft.candidate_checkers.length ? (
              <Card size="small" title={`Trace 证据候选约束（${traceCaseDraft.candidate_checkers.length}）`}>
                <List
                  size="small"
                  dataSource={traceCaseDraft.candidate_checkers}
                  renderItem={(candidate) => {
                    const inDiagnostic = traceCaseCheckers.some((item) => item.type === candidate.type && JSON.stringify(item) === JSON.stringify(candidate));
                    const inHard = traceCaseHardCheckers.some((item) => item.type === candidate.type && JSON.stringify(item) === JSON.stringify(candidate));
                    const evidence = candidate._evidence || {};
                    return (
                      <List.Item
                        actions={[
                          <Button key="diagnostic" size="small" onClick={() => addTraceCaseCandidate(candidate, 'diagnostic')} disabled={inDiagnostic}>加入 Diagnostic</Button>,
                          <Button key="hard" size="small" type="primary" ghost onClick={() => addTraceCaseCandidate(candidate, 'hard')} disabled={inHard}>提升为 Hard</Button>,
                        ]}
                      >
                        <List.Item.Meta
                          title={<Space><Tag color="blue">{candidate.type}</Tag>{inHard ? <Tag color="green">Hard</Tag> : inDiagnostic ? <Tag>Diagnostic</Tag> : null}</Space>}
                          description={<Space direction="vertical" size={0}><Text type="secondary">{candidate._reason || '由 Trace 证据生成，需人工确认'}</Text><Text type="secondary">证据：工具 {Array.isArray(evidence.observed_tools) ? evidence.observed_tools.join('、') : '未知'}；调用 {evidence.tool_calls || 0} 次；错误 {evidence.tool_errors || 0} 次</Text></Space>}
                        />
                      </List.Item>
                    );
                  }}
                />
              </Card>
            ) : null}
            <Card size="small" title="审核用例与 Checker">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Input value={traceCaseReviewName} onChange={(event) => setTraceCaseReviewName(event.target.value)} addonBefore="名称" />
                <Select
                  allowClear
                  value={traceCaseReviewProjectId || undefined}
                  onChange={(value) => setTraceCaseReviewProjectId(value || '')}
                  style={{ width: '100%' }}
                  placeholder="绑定评测项目后才能验证和发布"
                  options={traceCaseProjects.map((project) => ({ value: project.id, label: `${project.id} · ${project.version}` }))}
                />
                <CheckerEditor title="Diagnostic Checkers" value={traceCaseCheckers} onChange={setTraceCaseCheckers} />
                <CheckerEditor title="Hard Checkers（明确通过条件）" value={traceCaseHardCheckers} onChange={setTraceCaseHardCheckers} />
                                {traceCaseDraft.capture ? (
                  <Alert type="success" showIcon message={`已捕获 fixture：${traceCaseDraft.capture.project_id}，哈希 ${traceCaseDraft.capture.sha256.slice(0, 12)}…`} />
                ) : traceCaseFixturePreview ? (
                  <Card size="small" title="fixture 捕获预览">
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Space wrap>
                        <Tag color={traceCaseFixturePreview.project_exists ? 'error' : 'blue'}>
                          项目：{traceCaseFixturePreview.project_id}
                        </Tag>
                        <Text type="secondary">文件：{traceCaseFixturePreview.file_count}</Text>
                        <Text type="secondary">大小：{(traceCaseFixturePreview.total_bytes / 1024).toFixed(1)} KB</Text>
                      </Space>
                      {traceCaseFixturePreview.project_exists ? <Alert type="error" showIcon message="同名评测项目已存在，无法确认捕获" /> : null}
                      {traceCaseFixturePreview.warnings.map((warning) => <Alert key={warning} type="warning" showIcon message={warning} />)}
                      <Table
                        size="small"
                        rowKey="path"
                        pagination={{ pageSize: 8, showSizeChanger: false }}
                        scroll={{ y: 220 }}
                        dataSource={traceCaseFixturePreview.files}
                        columns={[
                          { title: '文件', dataIndex: 'path', ellipsis: true },
                          { title: '大小', dataIndex: 'size', width: 100, render: (value: number) => `${(value / 1024).toFixed(1)} KB` },
                        ]}
                      />
                      <Space>
                        <Button type="primary" onClick={captureTraceCase} loading={traceCaseActionLoading} disabled={traceCaseFixturePreview.project_exists}>确认并捕获 fixture</Button>
                        <Button onClick={previewTraceCase} loading={traceCaseActionLoading}>重新扫描</Button>
                      </Space>
                    </Space>
                  </Card>
                ) : (
                  <Button onClick={previewTraceCase} loading={traceCaseActionLoading}>预览 fixture 文件</Button>
                )}
                <Button onClick={saveTraceCaseReview} loading={traceCaseActionLoading}>保存审核内容</Button>
              </Space>
            </Card>            {traceCaseDraft.validation ? (
              <Alert
                type={traceCaseDraft.validation.passed ? 'success' : 'error'}
                showIcon
                message={`隔离试运行：${traceCaseDraft.validation.passed ? '通过' : '未通过'}（得分 ${(traceCaseDraft.validation.score * 100).toFixed(1)}%）`}
                description={traceCaseDraft.validation.error || undefined}
              />
            ) : null}
            <Space>
              <Button onClick={() => { setTraceCaseDraft(null); setTraceCaseFixturePreview(null); }}>重新选择 Trace</Button>
              <Button type="primary" onClick={validateTraceCase} loading={traceCaseActionLoading} disabled={!traceCaseDraft.case.project_id || traceCaseDraft.status === 'published'}>隔离试运行</Button>
              <Button type="primary" ghost onClick={publishTraceCase} loading={traceCaseActionLoading} disabled={traceCaseDraft.status !== 'validated'}>发布用例</Button>
            </Space>
          </Space>
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Alert type="info" showIcon message="Trace 只作为只读证据，先生成草稿，再绑定项目并隔离试运行，最后显式发布。" />            {traceCaseDrafts.length ? (
              <Card size="small" title={`未完成草稿 (${traceCaseDrafts.length})`}>
                <List
                  size="small"
                  dataSource={traceCaseDrafts}
                  renderItem={(draft) => (
                    <List.Item
                      actions={[
                        <Button key="restore" size="small" onClick={() => restoreTraceCaseDraft(draft)}>恢复</Button>,
                        <Popconfirm key="delete" title="删除这个草稿？" onConfirm={() => removeTraceCaseDraft(draft)} disabled={draft.status === 'published'}>
                          <Button size="small" danger disabled={draft.status === 'published'}>删除</Button>
                        </Popconfirm>,
                      ]}
                    >
                      <List.Item.Meta
                        title={draft.case.name || draft.case.id}
                        description={`${draft.status} · ${draft.session_id}`}
                      />
                    </List.Item>
                  )}
                />
              </Card>
            ) : null}
            <div>
              <Text strong>Trace 会话</Text>
              <Select
                showSearch
                value={traceCaseSessionId || undefined}
                onChange={setTraceCaseSessionId}
                style={{ width: '100%', marginTop: 8 }}
                placeholder="选择要转换的 Trace 会话"
                optionFilterProp="label"
                options={traceCaseTraces.map((trace) => ({ value: trace.session_id, label: `${trace.title || trace.session_id} · ${trace.events} 个事件` }))}
              />
            </div>
            <div>
              <Text strong>评测项目（用于隔离验证）</Text>
              <Select
                allowClear
                value={traceCaseProjectId || undefined}
                onChange={(value) => setTraceCaseProjectId(value || '')}
                style={{ width: '100%', marginTop: 8 }}
                placeholder="选择项目；未绑定时只能生成草稿"
                options={traceCaseProjects.map((project) => ({ value: project.id, label: `${project.id} · ${project.version}` }))}
              />
            </div>
            <div>
              <Text strong>用例名称（可选）</Text>
              <Input value={traceCaseName} onChange={(event) => setTraceCaseName(event.target.value)} placeholder="默认使用首条用户请求" style={{ marginTop: 8 }} />
            </div>
            <Button type="primary" onClick={createTraceCase} loading={traceCaseActionLoading} disabled={!traceCaseSessionId}>生成用例草稿</Button>
          </Space>
        )}
      </Modal>
    </Modal>
  );
};

export default EvaluationCenter;

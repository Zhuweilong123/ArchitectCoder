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
import {
  ACTIVE_BATCH_STORAGE_KEY, COMPARISON_VERSION_COLORS, EVAL_AGENT_LABEL,
  TRACE_SUITE, UNCLASSIFIED_SUITE,
  archiveExecutionTimestamp, fileName, fmtDuration, getCheckerDefinitions,
  fmtPromptCacheRate, fmtPromptPrefixReuseRate, fmtTime, traceSessionFromResult,
  validateCheckerConfigs, type CheckerDefinition, type CheckerFieldKind, type CheckerFieldSpec,
} from './evaluationUtils';
import {
  loadEvaluationOverview, loadTraceCaseFactoryResources,
} from '../../services/evaluationCenterApi';
import './EvaluationCenter.css';

const { Text } = Typography;
type EvaluationLanguage = 'en' | 'zh';
const tx = (language: EvaluationLanguage, zh: string, en: string) => language === 'en' ? en : zh;

function statusTag(status: string, passed?: boolean, language: EvaluationLanguage = 'zh'): React.ReactNode {
  if (status === 'passed' || passed) return <Tag color="success">{tx(language, '通过', 'Passed')}</Tag>;
  if (status === 'failed') return <Tag color="error">{tx(language, '失败', 'Failed')}</Tag>;
  if (status === 'timeout') return <Tag color="warning">{tx(language, '超时', 'Timeout')}</Tag>;
  if (status === 'running') return <Tag color="processing">{tx(language, '运行中', 'Running')}</Tag>;
  if (status === 'queued') return <Tag>{tx(language, '排队中', 'Queued')}</Tag>;
  return <Tag>{status || tx(language, '未知', 'Unknown')}</Tag>;
}

const FAILURE_CATEGORY_LABELS: Record<string, [string, string]> = {
  agent_failure: ['Agent', 'Agent'],
  tool_failure: ['工具', 'Tool'],
  environment_failure: ['环境', 'Environment'],
  checker_failure: ['Checker', 'Checker'],
  timeout: ['超时', 'Timeout'],
  budget_exceeded: ['预算', 'Budget'],
};

function failureCategoryTag(category?: string, language: EvaluationLanguage = 'zh'): React.ReactNode {
  if (!category || category === 'none') return <Text type="secondary">-</Text>;
  const label = FAILURE_CATEGORY_LABELS[category];
  return <Tag color="error">{label ? tx(language, label[0], label[1]) : category}</Tag>;
}

type EvaluationTab = 'overview' | 'performance' | 'comparison' | 'runs' | 'archives';

interface CheckerEditorProps {
  title: string;
  value: Array<Record<string, any>>;
  onChange: (value: Array<Record<string, any>>) => void;
}

const CheckerEditor: React.FC<CheckerEditorProps> = ({ title, value, onChange }) => {
  const interfaceLanguage = useUiStore((state) => state.interfaceLanguage);
  const definitions = getCheckerDefinitions(interfaceLanguage);
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
    <Card size="small" title={`${title} (${value.length})`} extra={<Button size="small" onClick={() => onChange([...value, { type: 'file_exists' }])}>{tx(interfaceLanguage, '添加 Checker', 'Add checker')}</Button>}>
      <Space direction="vertical" style={{ width: '100%' }}>
        {value.length === 0 ? <Text type="secondary">{tx(interfaceLanguage, '暂无配置，点击右上角添加 Checker', 'No configuration yet. Click Add checker above.')}</Text> : value.map((checker, index) => {
          const definition = definitions.find((item) => item.type === String(checker.type || '')) || definitions[0];
          return (
            <Card key={`${index}-${checker.type || 'checker'}`} size="small" type="inner" title={`Checker ${index + 1}`} extra={<Button danger size="small" onClick={() => onChange(value.filter((_, current) => current !== index))}>{tx(interfaceLanguage, '删除', 'Delete')}</Button>}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Select
                  showSearch
                  optionFilterProp="label"
                  value={checker.type || undefined}
                  style={{ width: '100%' }}
                  placeholder={tx(interfaceLanguage, '选择 Checker 类型', 'Select checker type')}
                  options={definitions.map((item) => ({ value: item.type, label: `${item.label} (${item.type})` }))}
                  onChange={(type) => onChange(value.map((item, current) => current === index ? { type } : item))}
                />
                <Space wrap style={{ width: '100%' }}>
                  {definition.fields.map((field) => {
                    const currentValue = checker[field.key];
                    const displayValue = field.kind === 'list' ? (Array.isArray(currentValue) ? currentValue.join(', ') : '') : field.kind === 'json' ? (currentValue === undefined ? '' : JSON.stringify(currentValue, null, 2)) : currentValue === undefined ? '' : String(currentValue);
                    const control = field.kind === 'json' ? (
                      <Input.TextArea value={displayValue} onChange={(event) => updateField(index, field, event.target.value)} autoSize={{ minRows: 1, maxRows: 4 }} placeholder={field.placeholder || tx(interfaceLanguage, 'JSON 值', 'JSON value')} />
                    ) : <Input value={displayValue} onChange={(event) => updateField(index, field, event.target.value)} type={field.kind === 'number' ? 'number' : 'text'} placeholder={field.placeholder || (field.kind === 'list' ? tx(interfaceLanguage, '多个值用逗号分隔', 'Separate multiple values with commas') : '')} />;
                    return <div key={field.key} style={{ minWidth: 220, flex: '1 1 220px' }}><Text type={field.required ? undefined : 'secondary'}>{field.label}{field.required ? ' *' : tx(interfaceLanguage, '（可选）', ' (optional)')}</Text>{control}</div>;
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
    clearTraceCaseFactoryRequest, evaluationVisible, traceCaseFactoryRequestedSessionId, setEvaluationVisible, setTraceSessionId, setTraceVisible, interfaceLanguage,
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
    label: value === UNCLASSIFIED_SUITE ? tx(interfaceLanguage, '未分类', 'Unclassified') : value,
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
  const selectedPerformanceCaseIds = useMemo(
    () => new Set(
      selectedRuns
        .flatMap((item) => item.case_ids || [])
        .filter((caseId) => baselineCaseIds.includes(caseId)),
    ),
    [baselineCaseIds, selectedRuns],
  );
  const selectedRunCaseIdsMatch = selectedRuns.length > 0
    && baselineCaseIds.length > 0
    && baselineCaseIds.every((caseId) => selectedPerformanceCaseIds.has(caseId));

  useEffect(() => {
    setSelectedSuites((current) => current.length > 0
      ? current.filter((value) => suites.includes(value))
      : suites);
  }, [suites]);

  const refresh = async () => {
    setLoading(true);
    try {
      const overview = await loadEvaluationOverview();
      setCases(overview.cases);
      setTrends(overview.trends);
      setArchives(overview.archives);
      setPerformanceRuns(overview.performanceRuns);
      setRepository(overview.repository);
      const storedBatchId = window.localStorage.getItem(ACTIVE_BATCH_STORAGE_KEY);
      const latestBatchId = storedBatchId || overview.trends[0]?.batch_id;
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
      setBaseline(overview.baseline);
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '评测数据加载失败', 'Failed to load evaluation data')}: ${error?.response?.data?.detail || error.message || error}`);
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
      message.error(`${tx(interfaceLanguage, '性能结果加载失败', 'Failed to load performance results')}: ${error?.response?.data?.detail || error.message || error}`);
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
        message.info(tx(interfaceLanguage, '该性能结果已被删除，列表已刷新', 'This performance result was deleted; the list has been refreshed'));
        return;
      }
      message.error(`${tx(interfaceLanguage, '性能结果详情加载失败', 'Failed to load performance details')}: ${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setPerformanceLoading(false);
    }
  };

  const openTrace = (result: EvalResult) => {
    const sessionId = traceSessionFromResult(result);
    if (!sessionId) {
      message.warning(tx(interfaceLanguage, '该用例没有可直达的 Trace 文件', 'This case has no directly accessible Trace file'));
      return;
    }
    setTraceSessionId(sessionId);
    setTraceVisible(true);
  };

  const archivePerformance = async () => {
    if (!selectedPerformance || !selectedPerformance.results?.length) return;
    const version = performanceVersion.trim();
    if (!version) {
      message.warning(tx(interfaceLanguage, '请先填写归档版本', 'Enter an archive version first'));
      return;
    }
    setPerformanceArchiving(true);
    try {
      await archiveEvalPerformanceResult(selectedPerformance.result_id, version, `${version} ${tx(interfaceLanguage, '性能评测归档', 'performance archive')}`);
      await refresh();
      setSelectedPerformance({ ...selectedPerformance, version, archived: true });
      message.success(tx(interfaceLanguage, '性能评测已归档', 'Performance evaluation archived'));
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '性能评测归档失败', 'Failed to archive performance evaluation')}: ${error?.response?.data?.detail || error.message || error}`);
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
      message.success(tx(interfaceLanguage, '性能结果本地数据已删除', 'Local performance result deleted'));
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '删除性能结果失败', 'Failed to delete performance result')}: ${error?.response?.data?.detail || error.message || error}`);
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
      message.success(tx(interfaceLanguage, '运行批次本地数据已删除', 'Local run deleted'));
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '删除运行批次失败', 'Failed to delete run')}: ${error?.response?.data?.detail || error.message || error}`);
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
      message.success(tx(interfaceLanguage, '评测用例草稿已删除', 'Evaluation case draft deleted'));
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '删除草稿失败', 'Failed to delete draft')}: ${error?.response?.data?.detail || error.message || error}`);
    }
  };
  const openTraceCaseFactory = async (requestedSessionId = "") => {
    setTraceCaseVisible(true);
    setTraceCaseDraft(null);
    setTraceCaseFixturePreview(null);
    setTraceCaseLoading(true);
    try {
      const resources = await loadTraceCaseFactoryResources();
      const { traces, projects, drafts } = resources;
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
      message.error(`${tx(interfaceLanguage, 'Trace 用例能力加载失败', 'Failed to load Trace case resources')}: ${error?.response?.data?.detail || error.message || error}`);
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
      message.warning(tx(interfaceLanguage, '请先选择一个 Trace 会话', 'Select a Trace session first'));
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
      message.success(tx(interfaceLanguage, 'Trace 已转换为评测用例草稿', 'Trace converted to an evaluation case draft'));
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, 'Trace 转换失败', 'Failed to convert Trace')}: ${error?.response?.data?.detail || error.message || error}`);
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
      message.error(`${tx(interfaceLanguage, 'fixture 预览失败', 'Failed to preview fixture')}: ${error?.response?.data?.detail || error.message || error}`);
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
      message.success(`${tx(interfaceLanguage, '工作区已捕获为 fixture', 'Workspace captured as fixture')} (${draft.capture?.file_count || 0} ${tx(interfaceLanguage, '个文件', 'files')})`);
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, 'fixture 捕获失败', 'Failed to capture fixture')}: ${error?.response?.data?.detail || error.message || error}`);
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
    message.success(target === 'hard' ? tx(interfaceLanguage, '候选约束已加入 Hard Checkers', 'Candidate added to Hard Checkers') : tx(interfaceLanguage, '候选约束已加入 Diagnostic Checkers', 'Candidate added to Diagnostic Checkers'));
  };
  const saveTraceCaseReview = async () => {
    if (!traceCaseDraft) return;
    try {
      validateCheckerConfigs(traceCaseCheckers, 'checkers');
      validateCheckerConfigs(traceCaseHardCheckers, 'hard_checkers');
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, 'Checker 配置无效', 'Invalid checker configuration')}: ${error.message || error}`);
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
      message.success(tx(interfaceLanguage, '草稿审核内容已保存，请重新执行隔离试运行', 'Draft review saved; run the isolated validation again'));
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '保存审核内容失败', 'Failed to save review')}: ${error?.response?.data?.detail || error.message || error}`);
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
        draft.status === 'validated' ? tx(interfaceLanguage, '隔离试运行通过，可以发布', 'Isolated validation passed; ready to publish') : tx(interfaceLanguage, '隔离试运行未通过，请检查结果', 'Isolated validation failed; check the results'),
      );
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '隔离试运行失败', 'Isolated validation failed')}: ${error?.response?.data?.detail || error.message || error}`);
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
      message.success(tx(interfaceLanguage, '评测用例已发布到本地目录', 'Evaluation case published to the local catalog'));
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '发布评测用例失败', 'Failed to publish evaluation case')}: ${error?.response?.data?.detail || error.message || error}`);
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
        message.success(`${tx(interfaceLanguage, '评测批次完成', 'Evaluation run complete')}: ${next.summary.passed}/${next.summary.completed} ${tx(interfaceLanguage, '通过', 'passed')}`);
      }
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '评测状态查询失败', 'Failed to query evaluation status')}: ${error?.response?.data?.detail || error.message || error}`);
    }
  };

  const runBatch = async () => {
    if (!repository?.version || repository.version === 'unknown') {
      message.warning(tx(interfaceLanguage, '无法获取当前仓库版本，暂时不能启动评测', 'Cannot read the current repository version; evaluation cannot start'));
      return;
    }
    if (!selectedCaseIds.length) {
      message.warning(tx(interfaceLanguage, '请至少选择一个评测集', 'Select at least one suite'));
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
      message.error(`${tx(interfaceLanguage, '启动评测失败', 'Failed to start evaluation')}: ${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setLoading(false);
    }
  };

  const archiveBatch = async () => {
    if (batch && ['running', 'queued'].includes(batch.status)) return;
    if (!archiveReady) {
      message.warning(tx(interfaceLanguage, '只有完整评测集全部执行完成后，才允许一键归档', 'Archive all is available only after the complete suite finishes'));
      return;
    }
    setArchiving(true);
    try {
      if (batch) await archiveEvalBatch(batch.batch_id, `${batch.version} ${batch.suite} ${tx(interfaceLanguage, '评测归档', 'evaluation archive')}`);
      else if (baseline) await archiveEvalBaseline(`${baseline.version} DevAgent ${tx(interfaceLanguage, '基线归档', 'baseline archive')}`);
      await refresh();
      message.success(tx(interfaceLanguage, '评测结果已归档', 'Evaluation results archived'));
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '归档失败', 'Archiving failed')}: ${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setArchiving(false);
    }
  };

  const resultColumns = [
    { title: tx(interfaceLanguage, '用例', 'Case'), dataIndex: 'case_id', key: 'case_id', ellipsis: true },
    { title: 'Agent', dataIndex: 'agent', key: 'agent', width: 100, render: () => EVAL_AGENT_LABEL },
    { title: tx(interfaceLanguage, '状态', 'Status'), dataIndex: 'status', key: 'status', width: 90, render: (v: string, row: EvalResult) => statusTag(v, row.passed, interfaceLanguage) },
    { title: tx(interfaceLanguage, '失败归因', 'Failure category'), dataIndex: 'failure_category', key: 'failure_category', width: 95, render: (value: string) => failureCategoryTag(value, interfaceLanguage) },
    { title: tx(interfaceLanguage, '得分', 'Score'), dataIndex: 'score', key: 'score', width: 80, render: (v: number) => `${(v * 100).toFixed(0)}%` },
    { title: tx(interfaceLanguage, '耗时', 'Duration'), dataIndex: 'duration_ms', key: 'duration_ms', width: 90, render: fmtDuration },
    { title: tx(interfaceLanguage, '模型', 'Model'), dataIndex: 'model', key: 'model', width: 130, ellipsis: true },
    { title: 'Trace', key: 'trace', width: 110, render: (_: unknown, row: EvalResult) => (
      <Space size={4}>
        <Text type="secondary" ellipsis style={{ maxWidth: 80 }}>{row.trace_id || '-'}</Text>
        {traceSessionFromResult(row) ? <Button size="small" type="link" onClick={(event) => { event.stopPropagation(); openTrace(row); }}>{tx(interfaceLanguage, '直达', 'Open')}</Button> : null}
      </Space>
    ) },
  ];

  const activeSummary = batch?.summary;
  const renderLegacyBaseline = () => baseline ? (
    <Card size="small" className="evaluation-baseline-card" title={<Space><LineChartOutlined />{tx(interfaceLanguage, '性能基线', 'Performance baseline')}</Space>} extra={<Space><Tag color="blue">{EVAL_AGENT_LABEL}</Tag><Text type="secondary">{baseline.version}</Text></Space>}>
      <div className="evaluation-baseline-scope">
        <Tag color="green">{tx(interfaceLanguage, '正式基线', 'Official baseline')} · {baselineCaseIds.length} {tx(interfaceLanguage, '个用例', 'cases')}</Tag>
        {cases.length !== baselineCaseIds.length ? <Tag>{tx(interfaceLanguage, '当前目录', 'Current catalog')} · {cases.length} {tx(interfaceLanguage, '个用例（含诊断集）', 'cases (including diagnostics)')}</Tag> : null}
      </div>
      <div className="evaluation-baseline-meta">{baseline.label} · {baseline.model} · {tx(interfaceLanguage, '快照时间', 'Captured')}: {fmtTime(baseline.captured_at)}</div>
      <Row gutter={[12, 12]} className="evaluation-stat-row">
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '用例数', 'Cases')} value={baseline.case_count} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '通过率', 'Pass rate')} value={baseline.pass_rate} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '平均得分', 'Average score')} value={baseline.average_score} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '通过 / 失败 / 超时 / 错误', 'Passed / Failed / Timeout / Errors')} value={`${baseline.passed} / ${baseline.failed} / ${baseline.timeout} / ${baseline.errors ?? 0}`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '累积耗时', 'Total duration')} value={fmtDuration(baseline.total_duration_ms)} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="Total Token" value={baseline.total_tokens} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '工具调用', 'Tool calls')} value={baseline.total_tool_calls} /></Col>
      </Row>
      <Table size="small" rowKey="name" pagination={false} columns={[
        { title: tx(interfaceLanguage, '范围', 'Scope'), dataIndex: 'name', key: 'name' },
        { title: tx(interfaceLanguage, '通过', 'Passed'), key: 'passed', render: (_: unknown, row: EvalBaseline['groups'][number]) => `${row.passed} / ${row.total}` },
        { title: tx(interfaceLanguage, '通过率', 'Pass rate'), dataIndex: 'pass_rate', key: 'pass_rate', render: (v: number) => `${(v * 100).toFixed(1)}%` },
        { title: tx(interfaceLanguage, '平均得分', 'Average score'), dataIndex: 'average_score', key: 'average_score', render: (v: number) => `${(v * 100).toFixed(1)}%` },
        { title: tx(interfaceLanguage, '失败 / 超时', 'Failed / Timeout'), key: 'issues', render: (_: unknown, row: EvalBaseline['groups'][number]) => `${row.failed} / ${row.timeout}` },
      ]} dataSource={baseline.groups} />
    </Card>
  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tx(interfaceLanguage, '暂无性能基线', 'No performance baseline')} />;

  const renderBaseline = () => {
    if (!baseline || baselineIsCurrent) return renderLegacyBaseline();
    return (
      <Card size="small" className="evaluation-baseline-card" title={<Space><LineChartOutlined />{tx(interfaceLanguage, '性能基线', 'Performance baseline')}</Space>} extra={<Tag color="warning">{tx(interfaceLanguage, '目录已更新，基线待重建', 'Catalog changed; baseline rebuild required')}</Tag>}>
        <Alert
          type="warning"
          showIcon
          message={tx(interfaceLanguage, '当前基线仍对应旧评测目录', 'The current baseline still points to an older evaluation catalog')}
          description={tx(interfaceLanguage, `当前目录有 ${cases.length} 个用例，旧基线快照仅覆盖 ${baseline.case_count} 个用例。旧基线指标不再展示为当前成绩，请完成全量评测后重新归档。`, `The current catalog has ${cases.length} cases, while the baseline snapshot covers only ${baseline.case_count}. Its metrics are not shown as current results; run the full evaluation and archive a new baseline.`)}
          style={{ marginBottom: 12 }}
        />
        <Table
          size="small"
          rowKey="name"
          pagination={false}
          columns={[
            { title: tx(interfaceLanguage, '当前评测范围', 'Current evaluation scope'), dataIndex: 'name', key: 'name' },
            { title: tx(interfaceLanguage, '用例数', 'Cases'), dataIndex: 'total', key: 'total' },
            { title: tx(interfaceLanguage, '基线状态', 'Baseline status'), key: 'status', render: () => <Tag color="warning">{tx(interfaceLanguage, '待重建', 'Rebuild required')}</Tag> },
          ]}
          dataSource={catalogGroups}
        />
      </Card>
    );
  };

  const renderBatch = () => batch ? (
    <Card size="small" title={tx(interfaceLanguage, '当前评测批次', 'Current evaluation run')}>
      <div className="evaluation-batch-line"><Text strong>{batch.version}</Text> · {batch.suite} · {statusTag(batch.status, undefined, interfaceLanguage)}{batch.current_case_id ? <Text type="secondary">{tx(interfaceLanguage, '当前', 'Current')}: {batch.current_case_id}</Text> : null}<Text type="secondary">{tx(interfaceLanguage, '开始', 'Started')}: {fmtTime(batch.started_at)}</Text></div>
      {batch.summary.total > 0 ? (() => {
        const active = batch.status === 'running' || batch.status === 'queued';
        const completed = !active && batch.status === 'completed'
          ? batch.summary.total
          : batch.summary.completed;
        return <Progress percent={Math.min(100, Math.round(completed / batch.summary.total * 100))} status={active ? 'active' : batch.status === 'completed' ? 'success' : 'exception'} />;
      })() : null}
      {batch.error ? <Alert type="error" showIcon message={batch.error} /> : null}
      <Row gutter={[12, 12]} className="evaluation-stat-row">
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '总用例', 'Total cases')} value={activeSummary?.total || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '已完成', 'Completed')} value={activeSummary?.completed || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '通过数', 'Passed')} value={activeSummary?.passed || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '失败数', 'Failed')} value={activeSummary?.failed || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '超时数', 'Timeouts')} value={activeSummary?.timeout || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '错误数', 'Errors')} value={activeSummary?.errors || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '通过率', 'Pass rate')} value={activeSummary?.pass_rate || 0} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '平均得分', 'Average score')} value={activeSummary?.average_score || 0} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '平均耗时', 'Average duration')} value={fmtDuration(activeSummary?.average_duration_ms || 0)} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title="Total Token" value={activeSummary?.total_tokens || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '工具调用', 'Tool calls')} value={activeSummary?.total_tool_calls || 0} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, 'Prompt 缓存命中率', 'Prompt cache hit rate')} value={fmtPromptCacheRate(activeSummary?.prompt_cache_hit_rate, tx(interfaceLanguage, '暂无数据', 'No data'))} /></Col>
        <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, 'Prompt 前缀复用率', 'Prompt prefix reuse rate')} value={fmtPromptPrefixReuseRate(activeSummary?.prompt_prefix_reuse_rate, tx(interfaceLanguage, '暂无数据', 'No data'))} /></Col>
      </Row>
      <Table size="small" rowKey="case_id" pagination={{ pageSize: 8 }} columns={resultColumns} dataSource={batch.results} onRow={(row) => ({ onClick: () => setSelectedCaseId(row.case_id) })} />
    </Card>
  ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tx(interfaceLanguage, '尚未运行评测批次', 'No evaluation run yet')} />;

  const performanceColumns = [
    { title: tx(interfaceLanguage, '版本', 'Version'), dataIndex: 'version', key: 'version', width: 220, render: (v: string) => v ? <Text className="evaluation-version-cell" ellipsis={{ tooltip: v }} title={v}>{v}</Text> : <Text type="secondary">{tx(interfaceLanguage, '未标记', 'Unlabeled')}</Text> },
    { title: tx(interfaceLanguage, '结果文件', 'Result file'), dataIndex: 'source_path', key: 'source_path', width: 220, render: (v: string, row: EvalPerformanceRun) => {
      const value = v || row.file_name || '-';
      return <Text className="evaluation-file-cell" ellipsis={{ tooltip: value }} title={value}>{fileName(value)}</Text>;
    } },
    { title: tx(interfaceLanguage, '执行时间', 'Run time'), dataIndex: 'started_at', key: 'started_at', width: 150, render: fmtTime },
    { title: tx(interfaceLanguage, '用例数', 'Cases'), dataIndex: 'result_count', key: 'result_count', width: 75 },
    { title: tx(interfaceLanguage, '通过率', 'Pass rate'), key: 'pass_rate', width: 90, render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.pass_rate * 100).toFixed(1)}%` },
    { title: tx(interfaceLanguage, '平均得分', 'Average score'), key: 'score', width: 100, render: (_: unknown, row: EvalPerformanceRun) => `${(row.summary.average_score * 100).toFixed(1)}%` },
    { title: tx(interfaceLanguage, '平均耗时', 'Average duration'), key: 'duration', width: 100, render: (_: unknown, row: EvalPerformanceRun) => fmtDuration(row.summary.average_duration_ms) },
    { title: 'Token', key: 'tokens', width: 110, render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tokens },
    { title: tx(interfaceLanguage, 'Prompt 缓存命中率', 'Prompt cache hit rate'), key: 'prompt_cache_hit_rate', width: 130, render: (_: unknown, row: EvalPerformanceRun) => fmtPromptCacheRate(row.summary.prompt_cache_hit_rate, tx(interfaceLanguage, '暂无数据', 'No data')) },
    { title: tx(interfaceLanguage, 'Prompt 前缀复用率', 'Prompt prefix reuse rate'), key: 'prompt_prefix_reuse_rate', width: 130, render: (_: unknown, row: EvalPerformanceRun) => fmtPromptPrefixReuseRate(row.summary.prompt_prefix_reuse_rate, tx(interfaceLanguage, '暂无数据', 'No data')) },
    { title: tx(interfaceLanguage, '归档', 'Archived'), key: 'archived', width: 90, render: (_: unknown, row: EvalPerformanceRun) => row.archived ? <Tag color="success">{tx(interfaceLanguage, '已归档', 'Archived')}</Tag> : <Tag>{tx(interfaceLanguage, '未归档', 'Pending')}</Tag> },
      { title: tx(interfaceLanguage, '操作', 'Actions'), key: 'action', width: 170, render: (_: unknown, row: EvalPerformanceRun) => (
        <Space>
          <Button size="small" onClick={(event) => { event.stopPropagation(); selectPerformance(row); }}>{tx(interfaceLanguage, '查看', 'View')}</Button>
          <Popconfirm
            title={tx(interfaceLanguage, '删除本地性能结果？', 'Delete local performance result?')}
            description={tx(interfaceLanguage, '将删除本地性能 JSONL 数据，已归档快照不受影响。', 'This deletes the local performance JSONL data; archived snapshots are not affected.')}
            okText={tx(interfaceLanguage, '确认删除', 'Delete')}
            cancelText={tx(interfaceLanguage, '取消', 'Cancel')}
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
              {tx(interfaceLanguage, '删除', 'Delete')}
            </Button>
          </Popconfirm>
        </Space>
      ) },
  ];

  const renderFailureDetail = () => selectedCase ? (
    <Card size="small" className="evaluation-case-detail" title={<Space>{tx(interfaceLanguage, '用例钻取', 'Case details')}: {selectedCase.case_id}{statusTag(selectedCase.status, selectedCase.passed, interfaceLanguage)}</Space>}>
      {selectedCase.failure_category && selectedCase.failure_category !== 'none' ? <div>{tx(interfaceLanguage, '失败归因', 'Failure category')}: {failureCategoryTag(selectedCase.failure_category, interfaceLanguage)}</div> : null}
      {selectedCase.error ? <Alert type="error" showIcon message={selectedCase.error} /> : null}
      <div className="evaluation-checker-list">{(selectedCase.checker_results || []).length === 0 ? <Text type="secondary">{tx(interfaceLanguage, '没有 checker 诊断信息', 'No checker diagnostics')}</Text> : (
        <List size="small" dataSource={selectedCase.checker_results} renderItem={(checker, index) => (
          <List.Item><Text>{String(checker.name || checker.checker || `checker-${index + 1}`)}</Text><Text type={checker.passed === false ? 'danger' : 'secondary'}>{checker.message || checker.detail || (checker.passed === false ? tx(interfaceLanguage, '未通过', 'Failed') : tx(interfaceLanguage, '通过', 'Passed'))}</Text></List.Item>
        )} />
      )}</div>
      {traceSessionFromResult(selectedCase) ? <Button icon={<EyeOutlined />} onClick={() => openTrace(selectedCase)}>{tx(interfaceLanguage, '打开 Trace', 'Open Trace')}</Button> : null}
    </Card>
  ) : null;

  const renderPerformance = () => (
    <>
      <Alert type="info" showIcon message={tx(interfaceLanguage, '先查看独立 performance JSONL 结果，再由你决定是否生成正式归档快照。', 'Review standalone performance JSONL results before creating an official archive snapshot.')} description={tx(interfaceLanguage, '原始结果不会被覆盖；归档后会出现在‘已归档’页签，并按评测执行时间倒序展示。', 'Raw results are preserved. Archived snapshots appear in the Archived tab, newest first by evaluation time.')} style={{ marginBottom: 12 }} />
      <Space wrap className="evaluation-filter-row"><Input.Search allowClear value={performanceQuery} onChange={(event) => setPerformanceQuery(event.target.value)} placeholder={tx(interfaceLanguage, '搜索版本、结果文件、评测集', 'Search version, result file, or suite')} style={{ width: 280 }} /><Select value={performanceArchiveFilter} onChange={setPerformanceArchiveFilter} style={{ width: 130 }} options={[{ value: 'all', label: tx(interfaceLanguage, '全部结果', 'All results') }, { value: 'pending', label: tx(interfaceLanguage, '待归档', 'Pending') }, { value: 'archived', label: tx(interfaceLanguage, '已归档', 'Archived') }]} /><Text type="secondary">{tx(interfaceLanguage, '已加载', 'Loaded')} {filteredPerformanceRuns.length} / {performanceRuns.length} {tx(interfaceLanguage, '个结果', 'results')}</Text></Space>
      <Table size="small" rowKey="result_id" loading={performanceLoading} dataSource={filteredPerformanceRuns} pagination={{ pageSize: 6 }} scroll={{ x: 1180 }} rowSelection={{ selectedRowKeys: comparisonIds, onChange: (keys) => setComparisonIds(keys as string[]) }} rowClassName={(row) => selectedPerformance?.result_id === row.result_id ? 'evaluation-selected-row' : ''} onRow={(row) => ({ onClick: (event) => { const target = event.target as HTMLElement; if (target.closest('button, a, [role="button"]')) return; selectPerformance(row); } })} columns={performanceColumns} />
      {selectedPerformance && (
        <Card size="small" title={<Space>{selectedPerformance.version || selectedPerformance.file_name}{selectedPerformance.archived ? <Tag color="success">{tx(interfaceLanguage, '已归档', 'Archived')}</Tag> : <Tag>{tx(interfaceLanguage, '待归档', 'Pending')}</Tag>}</Space>} style={{ marginTop: 12 }} extra={!selectedPerformance.archived ? (
          <Popconfirm title={tx(interfaceLanguage, '确认归档这份性能结果？', 'Archive this performance result?')} description={`${tx(interfaceLanguage, '版本', 'Version')}: ${performanceVersion || tx(interfaceLanguage, '未填写', 'Not set')}, ${tx(interfaceLanguage, '用例数', 'cases')}: ${selectedPerformance.summary.total}`} okText={tx(interfaceLanguage, '确认归档', 'Archive')} cancelText={tx(interfaceLanguage, '取消', 'Cancel')} onConfirm={archivePerformance}><Button type="primary" icon={<FileDoneOutlined />} loading={performanceArchiving}>{tx(interfaceLanguage, '确认归档', 'Archive')}</Button></Popconfirm>
        ) : null}>
          <Row gutter={[12, 12]} className="evaluation-stat-row">
            <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '用例数', 'Cases')} value={selectedPerformance.summary.total} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '通过率', 'Pass rate')} value={selectedPerformance.summary.pass_rate} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '平均得分', 'Average score')} value={selectedPerformance.summary.average_score} formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '平均耗时', 'Average duration')} value={fmtDuration(selectedPerformance.summary.average_duration_ms)} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title="Total Token" value={selectedPerformance.summary.total_tokens} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, '工具调用', 'Tool calls')} value={selectedPerformance.summary.total_tool_calls} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, 'Prompt 缓存命中率', 'Prompt cache hit rate')} value={fmtPromptCacheRate(selectedPerformance.summary.prompt_cache_hit_rate, tx(interfaceLanguage, '暂无数据', 'No data'))} /></Col>
            <Col xs={12} sm={8} md={4}><Statistic title={tx(interfaceLanguage, 'Prompt 前缀复用率', 'Prompt prefix reuse rate')} value={fmtPromptPrefixReuseRate(selectedPerformance.summary.prompt_prefix_reuse_rate, tx(interfaceLanguage, '暂无数据', 'No data'))} /></Col>
          </Row>
          {!selectedPerformance.archived ? <Space direction="vertical" style={{ width: '100%', marginBottom: 12 }}><Text type="secondary">{tx(interfaceLanguage, '归档版本（可修改）', 'Archive version (editable)')}</Text><Input value={performanceVersion} onChange={(event) => setPerformanceVersion(event.target.value)} placeholder="e.g. 3.3.1" /></Space> : null}
          <Space wrap className="evaluation-filter-row"><Input.Search allowClear value={resultQuery} onChange={(event) => setResultQuery(event.target.value)} placeholder={tx(interfaceLanguage, '搜索用例、模型、错误信息', 'Search case, model, or error')} style={{ width: 280 }} /><Select value={resultStatus} onChange={setResultStatus} style={{ width: 120 }} options={[{ value: 'all', label: tx(interfaceLanguage, '全部用例', 'All cases') }, { value: 'failed', label: tx(interfaceLanguage, '失败', 'Failed') }, { value: 'timeout', label: tx(interfaceLanguage, '超时', 'Timeout') }, { value: 'passed', label: tx(interfaceLanguage, '通过', 'Passed') }]} /><Text type="secondary">{tx(interfaceLanguage, '显示', 'Showing')} {filteredSelectedResults.length} / {selectedPerformance.results?.length || 0}</Text></Space>
          <Table size="small" rowKey="case_id" pagination={{ pageSize: 8 }} columns={resultColumns} dataSource={filteredSelectedResults} scroll={{ x: 950 }} onRow={(row) => ({ onClick: () => setSelectedCaseId(row.case_id) })} />
          {renderFailureDetail()}
        </Card>
      )}
    </>
  );

  const renderComparison = () => (
    <>
      <Alert type="info" showIcon message={tx(interfaceLanguage, '多版本对比', 'Multi-version comparison')} description={tx(interfaceLanguage, '在性能结果页勾选两份或多份结果后，这里会对比执行时间、通过率、得分、Token 和工具调用。', 'Select two or more results on the Performance results tab to compare run time, pass rate, score, Token, and tool calls.')} style={{ marginBottom: 12 }} />
      {comparisonRuns.length < 2 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tx(interfaceLanguage, '请先在‘性能结果’页勾选至少两个版本', 'Select at least two versions on the Performance results tab first')} /> : (
        <>
          <Table size="small" rowKey="result_id" pagination={false} dataSource={comparisonRuns} columns={[
            { title: tx(interfaceLanguage, '版本', 'Version'), dataIndex: 'version', key: 'version', render: (v: string, row: EvalPerformanceRun) => v || row.file_name },
            { title: tx(interfaceLanguage, '执行时间', 'Run time'), dataIndex: 'started_at', key: 'started_at', render: fmtTime },
            { title: tx(interfaceLanguage, '用例数', 'Cases'), dataIndex: 'result_count', key: 'result_count' },
            { title: tx(interfaceLanguage, '通过率', 'Pass rate'), key: 'pass_rate', render: (_: unknown, row: EvalPerformanceRun) => (row.summary.pass_rate * 100).toFixed(1) + '%' },
            { title: tx(interfaceLanguage, '平均得分', 'Average score'), key: 'score', render: (_: unknown, row: EvalPerformanceRun) => (row.summary.average_score * 100).toFixed(1) + '%' },
            { title: tx(interfaceLanguage, '平均耗时', 'Average duration'), key: 'duration', render: (_: unknown, row: EvalPerformanceRun) => fmtDuration(row.summary.average_duration_ms) },
            { title: 'Token', key: 'tokens', render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tokens },
            { title: tx(interfaceLanguage, '工具调用', 'Tool calls'), key: 'tools', render: (_: unknown, row: EvalPerformanceRun) => row.summary.total_tool_calls },
            { title: tx(interfaceLanguage, 'Prompt 缓存命中率', 'Prompt cache hit rate'), key: 'prompt_cache_hit_rate', render: (_: unknown, row: EvalPerformanceRun) => fmtPromptCacheRate(row.summary.prompt_cache_hit_rate, tx(interfaceLanguage, '暂无数据', 'No data')) },
            { title: tx(interfaceLanguage, 'Prompt 前缀复用率', 'Prompt prefix reuse rate'), key: 'prompt_prefix_reuse_rate', render: (_: unknown, row: EvalPerformanceRun) => fmtPromptPrefixReuseRate(row.summary.prompt_prefix_reuse_rate, tx(interfaceLanguage, '暂无数据', 'No data')) },
          ]} />
          <div className="evaluation-comparison-chart">
            <div className="evaluation-comparison-chart-header">
              <Text strong>{tx(interfaceLanguage, '选中版本性能对比', 'Selected version comparison')}</Text>
              <Text type="secondary">{tx(interfaceLanguage, '按构建时间从左到右排列，相邻版本标注数值变化', 'Sorted by build time; adjacent versions show the change')}</Text>
            </div>
            <div className="evaluation-comparison-chart-legend" aria-label={tx(interfaceLanguage, '版本颜色图例', 'Version color legend')}>
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
                { key: 'pass-rate', label: tx(interfaceLanguage, '通过率', 'Pass rate'), value: (run: EvalPerformanceRun) => run.summary.pass_rate * 100, format: (value: number) => value.toFixed(1) + '%', max: 100, higherIsBetter: true },
                { key: 'score', label: tx(interfaceLanguage, '平均得分', 'Average score'), value: (run: EvalPerformanceRun) => run.summary.average_score * 100, format: (value: number) => value.toFixed(1) + '%', max: 100, higherIsBetter: true },
                { key: 'duration', label: tx(interfaceLanguage, '平均耗时', 'Average duration'), value: (run: EvalPerformanceRun) => run.summary.average_duration_ms, format: (value: number) => fmtDuration(value), higherIsBetter: false },
                { key: 'tools', label: tx(interfaceLanguage, '工具调用', 'Tool calls'), value: (run: EvalPerformanceRun) => run.summary.total_tool_calls, format: (value: number) => value.toLocaleString(), higherIsBetter: false },
                { key: 'tokens', label: 'Token', value: (run: EvalPerformanceRun) => run.summary.total_tokens, format: (value: number) => value.toLocaleString(), higherIsBetter: false },
                { key: 'prompt-cache', label: tx(interfaceLanguage, 'Prompt 缓存命中率', 'Prompt cache hit rate'), value: (run: EvalPerformanceRun) => run.summary.prompt_cache_hit_rate === null || run.summary.prompt_cache_hit_rate === undefined ? null : run.summary.prompt_cache_hit_rate * 100, format: (value: number) => value.toFixed(1) + '%', max: 100, higherIsBetter: true },
                { key: 'prompt-prefix', label: tx(interfaceLanguage, 'Prompt 前缀复用率', 'Prompt prefix reuse rate'), value: (run: EvalPerformanceRun) => run.summary.prompt_prefix_reuse_rate === null || run.summary.prompt_prefix_reuse_rate === undefined ? null : run.summary.prompt_prefix_reuse_rate * 100, format: (value: number) => value.toFixed(1) + '%', max: 100, higherIsBetter: true },
              ].map((metric) => {
                const metricValues = comparisonRuns
                  .map(metric.value)
                  .filter((value): value is number => value !== null);
                const maxValue = metric.max || Math.max(...metricValues, 1);
                return (
                  <div className="evaluation-chart-metric" key={metric.key}>
                    <div className="evaluation-chart-metric-title">{metric.label}</div>
                    <div className="evaluation-chart-bars" role="img" aria-label={metric.label + tx(interfaceLanguage, '版本对比', ' by version')}>
                      {comparisonRuns.map((run, index) => {
                        const value = metric.value(run);
                        const previousValue = index > 0 ? metric.value(comparisonRuns[index - 1]) : null;
                        const change = value === null || previousValue === null || previousValue === 0
                          ? null
                          : ((value - previousValue) / Math.abs(previousValue)) * 100;
                        const slope = change === null || change === 0 ? 'flat' : change > 0 ? 'up' : 'down';
                        const sentiment = change === null || change === 0 ? 'flat' : metric.higherIsBetter === (change > 0) ? 'positive' : 'negative';
                        const height = value !== null && value > 0 ? Math.max((value / maxValue) * 100, 4) : 0;
                        const version = run.version || run.file_name;
                        const versionIndex = comparisonRuns.findIndex((candidate) => (candidate.version || candidate.file_name) === version);
                        const color = COMPARISON_VERSION_COLORS[versionIndex % COMPARISON_VERSION_COLORS.length];
                        return (
                          <React.Fragment key={run.result_id}>
                            {index > 0 ? (
                              <div className={'evaluation-chart-change evaluation-chart-change-slope-' + slope + ' evaluation-chart-change-' + sentiment} aria-label={tx(interfaceLanguage, '较上一版本变化', 'Change from previous version') + ' ' + (change === null ? tx(interfaceLanguage, '不可计算', 'N/A') : (change > 0 ? '+' : '') + change.toFixed(1) + '%')}>
                                <span className="evaluation-chart-change-line" />
                                <span className="evaluation-chart-change-label">{change === null ? '—' : (change > 0 ? '+' : '') + change.toFixed(1) + '%'}</span>
                              </div>
                            ) : null}
                              <div className="evaluation-chart-bar-item" title={version + ': ' + (value === null ? tx(interfaceLanguage, '暂无数据', 'No data') : metric.format(value))}>
                              <div className="evaluation-chart-bar-value">{value === null ? tx(interfaceLanguage, '暂无数据', 'No data') : metric.format(value)}</div>
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
  const renderRuns = () => trends.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tx(interfaceLanguage, '暂无批次记录', 'No run history')} /> : (
    <List size="small" dataSource={trends} renderItem={(item) => (
      <List.Item><List.Item.Meta title={<Space>{item.version}{statusTag(item.status, undefined, interfaceLanguage)}<Text type="secondary">{item.suite}</Text></Space>} description={fmtTime(item.started_at)} /><Space className="evaluation-trend-values"><Text>{tx(interfaceLanguage, '通过率', 'Pass rate')} {(item.summary.pass_rate * 100).toFixed(1)}%</Text><Text>{tx(interfaceLanguage, '得分', 'Score')} {(item.summary.average_score * 100).toFixed(1)}%</Text><Text>{tx(interfaceLanguage, '完成', 'Completed')} {item.summary.completed}/{item.summary.total}</Text><Text>{tx(interfaceLanguage, '失败', 'Failed')} {item.summary.failed}</Text><Text>{tx(interfaceLanguage, '超时', 'Timeout')} {item.summary.timeout}</Text><Text>{tx(interfaceLanguage, '耗时', 'Duration')} {fmtDuration(item.summary.average_duration_ms)}</Text><Text>Token {item.summary.total_tokens}</Text><Text>{tx(interfaceLanguage, '工具', 'Tools')} {item.summary.total_tool_calls}</Text></Space></List.Item>
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
        message.info(tx(interfaceLanguage, '该运行批次已被删除，列表已刷新', 'This run was deleted; the list has been refreshed'));
        return;
      }
      message.error(`${tx(interfaceLanguage, '历史批次详情加载失败', 'Failed to load run details')}: ${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setTrendLoading(false);
    }
  };

  const mergeSelectedRuns = async () => {
    if (selectedRuns.length < 1) return;
    setMergingRuns(true);
    try {
      const version = selectedRuns[0].version || repository?.version || 'working-tree';
      const merged = await mergeEvalBatches({
        batch_ids: selectedRuns.map((item) => item.batch_id),
        version,
        label: `${version} merged performance`,
      });
      setSelectedRunIds([]);
      message.success(`${selectedRuns.length > 1 ? tx(interfaceLanguage, '已合并', 'Merged') : tx(interfaceLanguage, '已转换', 'Converted')} ${merged.results.length} ${tx(interfaceLanguage, '个用例，并登记为性能结果', 'cases and registered as a performance result')}`);
      await refresh();
      setActiveTab('performance');
    } catch (error: any) {
      message.error(`${tx(interfaceLanguage, '批次合并失败', 'Failed to merge runs')}: ${error?.response?.data?.detail || error.message || error}`);
    } finally {
      setMergingRuns(false);
    }
  };

  const renderRunsWithDetails = () => trends.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tx(interfaceLanguage, '暂无批次记录', 'No run history')} /> : (
    <>
      <Alert
        type="info"
        showIcon
        message={tx(interfaceLanguage, '性能结果转换规则', 'Performance result conversion rules')}
        description={tx(interfaceLanguage, '所选批次合计覆盖完整性能基线用例集即可；其中额外的 Trace 或诊断用例会自动忽略。多个批次的版本必须一致，不同执行时间的批次可以合并。', 'Selected runs must collectively cover the complete performance baseline. Extra Trace or diagnostic cases are ignored. Versions must match; runs from different times can be merged.')}
        style={{ marginBottom: 12 }}
      />
      <Space wrap className="evaluation-filter-row">
        <Text type="secondary">{tx(interfaceLanguage, '已选', 'Selected')} {selectedRuns.length} {tx(interfaceLanguage, '个运行批次', 'runs')}</Text>
        <Text type="secondary">
          {selectedRuns.length > 0
            ? `${tx(interfaceLanguage, '将提取', 'Will extract')} ${selectedPerformanceCaseIds.size} ${tx(interfaceLanguage, '个性能基线用例', 'baseline cases')}`
            : tx(interfaceLanguage, '选择已完成的运行批次', 'Select completed runs')}
        </Text>
        <Button
          type="primary"
          disabled={selectedRuns.length < 1 || !selectedRunCaseIdsMatch || !selectedRunVersionsMatch || selectedRuns.some((item) => item.status !== 'completed')}
          loading={mergingRuns}
          onClick={mergeSelectedRuns}
        >
          {selectedRuns.length > 1 ? tx(interfaceLanguage, '合并为性能结果', 'Merge as performance result') : tx(interfaceLanguage, '转换为性能结果', 'Convert to performance result')}
        </Button>
      </Space>
      <List size="small" dataSource={trends} renderItem={(item) => (
      <React.Fragment key={item.batch_id}>
        <List.Item
          className="evaluation-trend-item"
          actions={[
            <Button type="link" loading={trendLoading && selectedTrendBatch?.batch_id === item.batch_id} onClick={(event) => { event.stopPropagation(); openTrend(item); }}>{selectedTrendBatch?.batch_id === item.batch_id ? tx(interfaceLanguage, '收起详情', 'Collapse details') : tx(interfaceLanguage, '查看详情', 'View details')}</Button>,
            <Popconfirm
              title={tx(interfaceLanguage, '删除本地运行批次？', 'Delete local run?')}
              description={tx(interfaceLanguage, '将删除本地批次记录及其运行数据，不会修改代码仓或基线文件。', 'This deletes the local run record and data without changing the repository or baseline files.')}
              okText={tx(interfaceLanguage, '确认删除', 'Delete')}
              cancelText={tx(interfaceLanguage, '取消', 'Cancel')}
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
                {tx(interfaceLanguage, '删除', 'Delete')}
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
              title={<Space className="evaluation-run-heading"><Text className="evaluation-version-cell" ellipsis={{ tooltip: item.version }} title={item.version}>{item.version}</Text>{statusTag(item.status, undefined, interfaceLanguage)}<Text type="secondary">{item.suite}</Text></Space>}
              description={fmtTime(item.started_at)}
            />
            <Space className="evaluation-trend-values"><Text>{tx(interfaceLanguage, '通过率', 'Pass rate')} {(item.summary.pass_rate * 100).toFixed(1)}%</Text><Text>{tx(interfaceLanguage, '得分', 'Score')} {(item.summary.average_score * 100).toFixed(1)}%</Text><Text>{tx(interfaceLanguage, '完成', 'Completed')} {item.summary.completed}/{item.summary.total}</Text><Text>{tx(interfaceLanguage, '失败', 'Failed')} {item.summary.failed}</Text><Text>{tx(interfaceLanguage, '超时', 'Timeout')} {item.summary.timeout}</Text><Text>{tx(interfaceLanguage, '耗时', 'Duration')} {fmtDuration(item.summary.average_duration_ms)}</Text><Text>Token {item.summary.total_tokens}</Text><Text>{tx(interfaceLanguage, '工具', 'Tools')} {item.summary.total_tool_calls}</Text></Space>
          </div>
          </div>
        </List.Item>
        {selectedTrendBatch?.batch_id === item.batch_id ? (
          <List.Item>
            <Card size="small" title={`${tx(interfaceLanguage, '批次详情', 'Run details')} · ${item.version} · ${item.suite}`} style={{ width: '100%' }}>
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
      <Space wrap className="evaluation-filter-row"><Input.Search allowClear value={archiveQuery} onChange={(event) => setArchiveQuery(event.target.value)} placeholder={tx(interfaceLanguage, '搜索版本、评测集、归档说明', 'Search version, suite, or archive note')} style={{ width: 300 }} /><Text type="secondary">{tx(interfaceLanguage, '最新执行的归档展示在最上面', 'Most recent archives appear first')}</Text></Space>
      {filteredArchives.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tx(interfaceLanguage, '暂无归档快照', 'No archive snapshots')} /> : (
        <Table size="small" rowKey="archive_id" pagination={{ pageSize: 5 }} dataSource={filteredArchives} columns={[
          { title: tx(interfaceLanguage, '版本', 'Version'), dataIndex: 'version', key: 'version' },
          { title: tx(interfaceLanguage, '评测集', 'Suite'), dataIndex: 'suite', key: 'suite' },
          { title: tx(interfaceLanguage, '通过率', 'Pass rate'), key: 'rate', render: (_: unknown, r: EvalArchive) => `${(r.summary.pass_rate * 100).toFixed(1)}%` },
          { title: tx(interfaceLanguage, '平均得分', 'Average score'), key: 'score', render: (_: unknown, r: EvalArchive) => `${(r.summary.average_score * 100).toFixed(1)}%` },
          { title: tx(interfaceLanguage, '失败/超时/错误', 'Failed / Timeout / Errors'), key: 'issues', render: (_: unknown, r: EvalArchive) => `${r.summary.failed} / ${r.summary.timeout} / ${r.summary.errors}` },
          { title: tx(interfaceLanguage, '平均耗时', 'Average duration'), key: 'duration', render: (_: unknown, r: EvalArchive) => fmtDuration(r.summary.average_duration_ms) },
          { title: 'Token', key: 'tokens', render: (_: unknown, r: EvalArchive) => r.summary.total_tokens },
          { title: tx(interfaceLanguage, '评测执行时间', 'Evaluation time'), dataIndex: 'started_at', key: 'started_at', render: fmtTime },
          { title: 'Archive ID', dataIndex: 'archive_id', key: 'archive_id', ellipsis: true },
        ]} scroll={{ x: 1050 }} />
      )}
    </>
  );

  return (
    <Modal className="evaluation-center-modal" title={<Space><LineChartOutlined />{tx(interfaceLanguage, 'ArchitectCoder 能力基准中心', 'ArchitectCoder Evaluation Center')}</Space>} open={evaluationVisible} onCancel={() => setEvaluationVisible(false)} footer={null} width={1220} styles={{ body: { maxHeight: 'calc(100vh - 150px)', overflowY: 'auto' } }}>
      <Card size="small" className="evaluation-control-card">
        <Space wrap>
          <Tag color="blue">{EVAL_AGENT_LABEL}</Tag>
          <Select
            mode="multiple"
            value={selectedSuites}
            onChange={setSelectedSuites}
            style={{ minWidth: 280, maxWidth: 420 }}
            maxTagCount="responsive"
            placeholder={tx(interfaceLanguage, '选择评测集', 'Select suites')}
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
                    {tx(interfaceLanguage, '全选评测集', 'Select all suites')} ({suites.length})
                  </Checkbox>
                </div>
              </>
            )}
          />
          <Tag color="green">{tx(interfaceLanguage, '正式基线', 'Official baseline')} · {baselineCaseIds.length}</Tag>
          <Tag color={selectedCaseIds.length === cases.length && cases.length > 0 ? 'green' : 'blue'}>{tx(interfaceLanguage, '当前选择', 'Selected')} {selectedCaseIds.length} / {cases.length || '-'}</Tag>
          <Tag color={repository?.dirty ? 'warning' : 'green'} title={repository?.commit || undefined}>{repository ? `${repository.branch}@${repository.commit.slice(0, 12)}${repository.dirty ? ' · dirty' : ''}` : tx(interfaceLanguage, '读取仓库版本中…', 'Reading repository version…')}</Tag>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={runBatch} loading={loading} disabled={!selectedCaseIds.length || !repository?.version || repository.version === 'unknown'}>{tx(interfaceLanguage, '一键运行', 'Run all')}</Button>
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>{tx(interfaceLanguage, '刷新', 'Refresh')}</Button>
          <Button icon={<FileAddOutlined />} onClick={() => openTraceCaseFactory()}>{tx(interfaceLanguage, 'Trace 转用例', 'Trace to case')}</Button>
          <Button icon={<EyeOutlined />} onClick={openPerformance}>{tx(interfaceLanguage, '性能结果', 'Performance results')}</Button>
          <Popconfirm title={tx(interfaceLanguage, '确认归档当前完整评测？', 'Archive the current complete evaluation?')} description={tx(interfaceLanguage, '只有全部评测集执行完成后才允许归档，原始结果不会被覆盖。', 'Archiving is available only after all suites finish; raw results are preserved.')} okText={tx(interfaceLanguage, '确认归档', 'Archive')} cancelText={tx(interfaceLanguage, '取消', 'Cancel')} onConfirm={archiveBatch}><Button icon={<FileDoneOutlined />} loading={archiving} disabled={!archiveReady}>{tx(interfaceLanguage, '一键归档', 'Archive all')}</Button></Popconfirm>
        </Space>
        <div className="evaluation-hint">{tx(interfaceLanguage, '支持组合评测集运行；“全选”会执行当前目录下全部评测用例。只有全部用例完成后才允许一键归档。版本自动取当前 Git 分支和 HEAD commit。', 'Run multiple suites together. “Select all” runs every case in the current catalog. Archive is available only after all cases finish. The version is read from the current Git branch and HEAD commit.')}</div>
      </Card>
      <Tabs activeKey={activeTab} onChange={(key) => setActiveTab(key as EvaluationTab)} items={[
        { key: 'overview', label: tx(interfaceLanguage, '概览与当前批次', 'Overview & current run'), children: <>{renderBaseline()}<Divider orientation="left">{tx(interfaceLanguage, '当前批次', 'Current run')}</Divider>{renderBatch()}</> },
        { key: 'runs', label: tx(interfaceLanguage, '运行批次', 'Runs'), children: renderRunsWithDetails() },
        { key: 'performance', label: `${tx(interfaceLanguage, '性能结果', 'Performance')} (${performanceRuns.length})`, children: renderPerformance() },
        { key: 'comparison', label: `${tx(interfaceLanguage, '多版本对比', 'Comparison')}${comparisonRuns.length ? ` (${comparisonRuns.length})` : ''}`, children: renderComparison() },
        { key: 'archives', label: `${tx(interfaceLanguage, '已归档', 'Archives')} (${archives.length})`, children: renderArchives() },
      ]} />
      <Modal
        title={tx(interfaceLanguage, 'Trace 一键转评测用例', 'Convert Trace to evaluation case')}
        open={traceCaseVisible}
        onCancel={() => setTraceCaseVisible(false)}
        footer={null}
        width={760}
      >
        {traceCaseLoading ? <div style={{ padding: 24, textAlign: 'center' }}>{tx(interfaceLanguage, '加载 Trace 与评测项目中…', 'Loading Trace sessions and evaluation projects…')}</div> : traceCaseDraft ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space wrap>
              <Tag color={traceCaseDraft.status === 'validated' ? 'green' : traceCaseDraft.status === 'published' ? 'blue' : 'orange'}>
                {traceCaseDraft.status}
              </Tag>
              <Text type="secondary">{tx(interfaceLanguage, '会话', 'Session')}: {traceCaseDraft.session_id}</Text>
              <Text type="secondary">{tx(interfaceLanguage, '事件', 'Events')}: {traceCaseDraft.trace_summary.events || 0}</Text>
              <Text type="secondary">{tx(interfaceLanguage, '工具调用', 'Tool calls')}: {traceCaseDraft.trace_summary.tool_calls || 0}</Text>
            </Space>
            {traceCaseDraft.warnings.map((warning) => <Alert key={warning} type="warning" showIcon message={warning} />)}
            <Card size="small" title={traceCaseDraft.case.name}>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>{traceCaseDraft.case.prompt || traceCaseDraft.case.turns?.map((turn) => turn.prompt).join('\n\n')}</Typography.Paragraph>
              <Text type="secondary">{tx(interfaceLanguage, '项目', 'Project')}: {traceCaseDraft.case.project_id || tx(interfaceLanguage, '未绑定', 'Unbound')}; {tx(interfaceLanguage, '候选约束', 'Candidate constraints')}: {traceCaseDraft.candidate_checkers.length}</Text>
            </Card>
            {traceCaseDraft.candidate_checkers.length ? (
              <Card size="small" title={`${tx(interfaceLanguage, 'Trace 证据候选约束', 'Trace evidence constraints')} (${traceCaseDraft.candidate_checkers.length})`}>
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
                          <Button key="diagnostic" size="small" onClick={() => addTraceCaseCandidate(candidate, 'diagnostic')} disabled={inDiagnostic}>{tx(interfaceLanguage, '加入 Diagnostic', 'Add to Diagnostic')}</Button>,
                          <Button key="hard" size="small" type="primary" ghost onClick={() => addTraceCaseCandidate(candidate, 'hard')} disabled={inHard}>{tx(interfaceLanguage, '提升为 Hard', 'Promote to Hard')}</Button>,
                        ]}
                      >
                        <List.Item.Meta
                          title={<Space><Tag color="blue">{candidate.type}</Tag>{inHard ? <Tag color="green">Hard</Tag> : inDiagnostic ? <Tag>Diagnostic</Tag> : null}</Space>}
                          description={<Space direction="vertical" size={0}><Text type="secondary">{candidate._reason || tx(interfaceLanguage, '由 Trace 证据生成，需人工确认', 'Generated from Trace evidence; manual confirmation required')}</Text><Text type="secondary">{tx(interfaceLanguage, '证据', 'Evidence')}: {tx(interfaceLanguage, '工具', 'Tools')} {Array.isArray(evidence.observed_tools) ? evidence.observed_tools.join(', ') : tx(interfaceLanguage, '未知', 'Unknown')}; {tx(interfaceLanguage, '调用', 'Calls')} {evidence.tool_calls || 0}; {tx(interfaceLanguage, '错误', 'Errors')} {evidence.tool_errors || 0}</Text></Space>}
                        />
                      </List.Item>
                    );
                  }}
                />
              </Card>
            ) : null}
            <Card size="small" title={tx(interfaceLanguage, '审核用例与 Checker', 'Review case and checkers')}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Input value={traceCaseReviewName} onChange={(event) => setTraceCaseReviewName(event.target.value)} addonBefore={tx(interfaceLanguage, '名称', 'Name')} />
                <Select
                  allowClear
                  value={traceCaseReviewProjectId || undefined}
                  onChange={(value) => setTraceCaseReviewProjectId(value || '')}
                  style={{ width: '100%' }}
                  placeholder={tx(interfaceLanguage, '绑定评测项目后才能验证和发布', 'Bind an evaluation project before validating or publishing')}
                  options={traceCaseProjects.map((project) => ({ value: project.id, label: `${project.id} · ${project.version}` }))}
                />
                <CheckerEditor title="Diagnostic Checkers" value={traceCaseCheckers} onChange={setTraceCaseCheckers} />
                <CheckerEditor title={tx(interfaceLanguage, 'Hard Checkers（明确通过条件）', 'Hard Checkers (explicit pass conditions)')} value={traceCaseHardCheckers} onChange={setTraceCaseHardCheckers} />
                                {traceCaseDraft.capture ? (
                  <Alert type="success" showIcon message={`${tx(interfaceLanguage, '已捕获 fixture', 'Fixture captured')}: ${traceCaseDraft.capture.project_id}; ${tx(interfaceLanguage, '哈希', 'hash')} ${traceCaseDraft.capture.sha256.slice(0, 12)}…`} />
                ) : traceCaseFixturePreview ? (
                  <Card size="small" title={tx(interfaceLanguage, 'fixture 捕获预览', 'Fixture capture preview')}>
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Space wrap>
                        <Tag color={traceCaseFixturePreview.project_exists ? 'error' : 'blue'}>
                          {tx(interfaceLanguage, '项目', 'Project')}: {traceCaseFixturePreview.project_id}
                        </Tag>
                        <Text type="secondary">{tx(interfaceLanguage, '文件', 'Files')}: {traceCaseFixturePreview.file_count}</Text>
                        <Text type="secondary">{tx(interfaceLanguage, '大小', 'Size')}: {(traceCaseFixturePreview.total_bytes / 1024).toFixed(1)} KB</Text>
                      </Space>
                      {traceCaseFixturePreview.project_exists ? <Alert type="error" showIcon message={tx(interfaceLanguage, '同名评测项目已存在，无法确认捕获', 'A project with the same name already exists; capture cannot be confirmed')} /> : null}
                      {traceCaseFixturePreview.warnings.map((warning) => <Alert key={warning} type="warning" showIcon message={warning} />)}
                      <Table
                        size="small"
                        rowKey="path"
                        pagination={{ pageSize: 8, showSizeChanger: false }}
                        scroll={{ y: 220 }}
                        dataSource={traceCaseFixturePreview.files}
                        columns={[
                          { title: tx(interfaceLanguage, '文件', 'File'), dataIndex: 'path', ellipsis: true },
                          { title: tx(interfaceLanguage, '大小', 'Size'), dataIndex: 'size', width: 100, render: (value: number) => `${(value / 1024).toFixed(1)} KB` },
                        ]}
                      />
                      <Space>
                        <Button type="primary" onClick={captureTraceCase} loading={traceCaseActionLoading} disabled={traceCaseFixturePreview.project_exists}>{tx(interfaceLanguage, '确认并捕获 fixture', 'Confirm and capture fixture')}</Button>
                        <Button onClick={previewTraceCase} loading={traceCaseActionLoading}>{tx(interfaceLanguage, '重新扫描', 'Rescan')}</Button>
                      </Space>
                    </Space>
                  </Card>
                ) : (
                  <Button onClick={previewTraceCase} loading={traceCaseActionLoading}>{tx(interfaceLanguage, '预览 fixture 文件', 'Preview fixture files')}</Button>
                )}
                <Button onClick={saveTraceCaseReview} loading={traceCaseActionLoading}>{tx(interfaceLanguage, '保存审核内容', 'Save review')}</Button>
              </Space>
            </Card>            {traceCaseDraft.validation ? (
              <Alert
                type={traceCaseDraft.validation.passed ? 'success' : 'error'}
                showIcon
                message={`${tx(interfaceLanguage, '隔离试运行', 'Isolated run')}: ${traceCaseDraft.validation.passed ? tx(interfaceLanguage, '通过', 'Passed') : tx(interfaceLanguage, '未通过', 'Failed')} (${tx(interfaceLanguage, '得分', 'Score')} ${(traceCaseDraft.validation.score * 100).toFixed(1)}%)`}
                description={traceCaseDraft.validation.error || undefined}
              />
            ) : null}
            <Space>
              <Button onClick={() => { setTraceCaseDraft(null); setTraceCaseFixturePreview(null); }}>{tx(interfaceLanguage, '重新选择 Trace', 'Choose another Trace')}</Button>
              <Button type="primary" onClick={validateTraceCase} loading={traceCaseActionLoading} disabled={!traceCaseDraft.case.project_id || traceCaseDraft.status === 'published'}>{tx(interfaceLanguage, '隔离试运行', 'Isolated run')}</Button>
              <Button type="primary" ghost onClick={publishTraceCase} loading={traceCaseActionLoading} disabled={traceCaseDraft.status !== 'validated'}>{tx(interfaceLanguage, '发布用例', 'Publish case')}</Button>
            </Space>
          </Space>
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Alert type="info" showIcon message={tx(interfaceLanguage, 'Trace 只作为只读证据，先生成草稿，再绑定项目并隔离试运行，最后显式发布。', 'Trace is read-only evidence. Create a draft, bind a project, run it in isolation, then publish explicitly.')} />            {traceCaseDrafts.length ? (
              <Card size="small" title={`${tx(interfaceLanguage, '未完成草稿', 'Drafts')} (${traceCaseDrafts.length})`}>
                <List
                  size="small"
                  dataSource={traceCaseDrafts}
                  renderItem={(draft) => (
                    <List.Item
                      actions={[
                        <Button key="restore" size="small" onClick={() => restoreTraceCaseDraft(draft)}>{tx(interfaceLanguage, '恢复', 'Restore')}</Button>,
                        <Popconfirm key="delete" title={tx(interfaceLanguage, '删除这个草稿？', 'Delete this draft?')} onConfirm={() => removeTraceCaseDraft(draft)} disabled={draft.status === 'published'}>
                          <Button size="small" danger disabled={draft.status === 'published'}>{tx(interfaceLanguage, '删除', 'Delete')}</Button>
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
              <Text strong>Trace {tx(interfaceLanguage, '会话', 'session')}</Text>
              <Select
                showSearch
                value={traceCaseSessionId || undefined}
                onChange={setTraceCaseSessionId}
                style={{ width: '100%', marginTop: 8 }}
                placeholder={tx(interfaceLanguage, '选择要转换的 Trace 会话', 'Select a Trace session to convert')}
                optionFilterProp="label"
                options={traceCaseTraces.map((trace) => ({ value: trace.session_id, label: `${trace.title || trace.session_id} · ${trace.events} ${tx(interfaceLanguage, '个事件', 'events')}` }))}
              />
            </div>
            <div>
              <Text strong>{tx(interfaceLanguage, '评测项目（用于隔离验证）', 'Evaluation project (for isolated validation)')}</Text>
              <Select
                allowClear
                value={traceCaseProjectId || undefined}
                onChange={(value) => setTraceCaseProjectId(value || '')}
                style={{ width: '100%', marginTop: 8 }}
                placeholder={tx(interfaceLanguage, '选择项目；未绑定时只能生成草稿', 'Select a project; without one, only a draft can be created')}
                options={traceCaseProjects.map((project) => ({ value: project.id, label: `${project.id} · ${project.version}` }))}
              />
            </div>
            <div>
              <Text strong>{tx(interfaceLanguage, '用例名称（可选）', 'Case name (optional)')}</Text>
              <Input value={traceCaseName} onChange={(event) => setTraceCaseName(event.target.value)} placeholder={tx(interfaceLanguage, '默认使用首条用户请求', 'Defaults to the first user request')} style={{ marginTop: 8 }} />
            </div>
            <Button type="primary" onClick={createTraceCase} loading={traceCaseActionLoading} disabled={!traceCaseSessionId}>{tx(interfaceLanguage, '生成用例草稿', 'Create case draft')}</Button>
          </Space>
        )}
      </Modal>
    </Modal>
  );
};

export default EvaluationCenter;

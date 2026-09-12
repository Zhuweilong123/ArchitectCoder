import type { EvalArchive, EvalResult } from '../../services/api';

export const EVAL_AGENT_LABEL = 'DevAgent';
export const UNCLASSIFIED_SUITE = '__unclassified__';
export const TRACE_SUITE = 'trace-3.1';
export const ACTIVE_BATCH_STORAGE_KEY = 'evaluationActiveBatchId';
export const COMPARISON_VERSION_COLORS = ['#1677ff', '#52c41a', '#faad14', '#722ed1', '#eb2f96', '#13c2c2'];

export function fmtDuration(ms: number): string {
  if (!ms) return '-';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function fmtPromptCacheRate(value: number | null | undefined, emptyValue = '暂无数据'): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(1)}%`
    : emptyValue;
}

export function fmtPromptPrefixReuseRate(value: number | null | undefined, emptyValue = '暂无数据'): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(1)}%`
    : emptyValue;
}

export function fmtTime(value: string): string {
  if (!value) return '-';
  return new Date(value).toLocaleString();
}

export function fileName(value: string): string {
  return value.split(/[\\/]/).pop() || value;
}

export function archiveExecutionTimestamp(archive: EvalArchive): number {
  const timestamp = Date.parse(archive.started_at || archive.created_at);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

export function traceSessionFromResult(result: EvalResult): string | null {
  const path = result.trace_path || '';
  const traceFileName = path.split(/[\\/]/).pop() || '';
  if (traceFileName.startsWith('trace_') && traceFileName.endsWith('.jsonl')) {
    return traceFileName.slice('trace_'.length, -'.jsonl'.length);
  }
  return null;
}

export type CheckerFieldKind = 'text' | 'list' | 'number' | 'json';
export type CheckerFieldSpec = {
  key: string;
  label: string;
  kind?: CheckerFieldKind;
  required?: boolean;
  placeholder?: string;
};
export type CheckerDefinition = { type: string; label: string; fields: CheckerFieldSpec[] };

const CHECKER_ENGLISH_LABELS: Record<string, { label: string; fields: Record<string, string> }> = {
  file_exists: { label: 'File exists', fields: { path: 'Path' } },
  file_absent: { label: 'File does not exist', fields: { path: 'Path' } },
  file_contains: { label: 'File contains text', fields: { path: 'Path', text: 'Text' } },
  file_not_contains: { label: 'File does not contain text', fields: { path: 'Path', text: 'Text' } },
  json_field: { label: 'JSON field', fields: { path: 'Path', field: 'Field path', expected: 'Expected value' } },
  pytest: { label: 'pytest', fields: { path: 'Test path', args: 'Arguments', timeout: 'Timeout (seconds)' } },
  hidden_pytest: { label: 'Hidden pytest', fields: { path: 'Test path', args: 'Arguments', timeout: 'Timeout (seconds)' } },
  answer_contains_all: { label: 'Answer contains all', fields: { texts: 'Required content' } },
  answer_ordered_contains: { label: 'Answer contains in order', fields: { texts: 'Ordered content' } },
  trace_policy: { label: 'Trace tool policy', fields: { max_tool_calls: 'Max tool calls', required_tools: 'Required tools', forbidden_tools: 'Forbidden tools' } },
  paths_unchanged: { label: 'Paths unchanged', fields: { paths: 'Paths' } },
  uml_valid: { label: 'Valid UML', fields: { path: 'Path', diagram: 'Diagram name' } },
  uml_contains: { label: 'UML contains element', fields: { path: 'Path', kind: 'Element type', name: 'Element name', diagram: 'Diagram name' } },
  uml_absent: { label: 'UML excludes element', fields: { path: 'Path', kind: 'Element type', name: 'Element name', diagram: 'Diagram name', class_name: 'Class name', method: 'Method name' } },
  uml_component_names: { label: 'UML component names', fields: { path: 'Path', names: 'Component names', diagram: 'Diagram name' } },
  uml_relation: { label: 'UML relation', fields: { path: 'Path', source: 'Source element', target: 'Target element', relation_type: 'Relation type', diagram: 'Diagram name' } },
  uml_method: { label: 'UML method', fields: { path: 'Path', class_name: 'Class name', method: 'Method name', diagram: 'Diagram name' } },
  uml_method_signature: { label: 'UML method signature', fields: { path: 'Path', class_name: 'Class name', method: 'Method name', params: 'Parameters', return_type: 'Return type', diagram: 'Diagram name' } },
  uml_sequence: { label: 'UML sequence contains', fields: { path: 'Path', labels: 'Sequence labels', diagram: 'Diagram name' } },
  uml_sequence_exact: { label: 'Exact UML sequence match', fields: { path: 'Path', labels: 'Exact labels', diagram: 'Diagram name' } },
};

export function getCheckerDefinitions(language: 'en' | 'zh'): CheckerDefinition[] {
  if (language === 'zh') return TRACE_CHECKER_DEFINITIONS;
  return TRACE_CHECKER_DEFINITIONS.map((definition) => {
    const translated = CHECKER_ENGLISH_LABELS[definition.type];
    if (!translated) return definition;
    return {
      ...definition,
      label: translated.label,
      fields: definition.fields.map((field) => ({
        ...field,
        label: translated.fields[field.key] || field.label,
        placeholder: field.placeholder === '测试路径' ? '.' : field.placeholder,
      })),
    };
  });
}

export const TRACE_CHECKER_DEFINITIONS: CheckerDefinition[] = [
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

export const checkerDefinitions = Object.fromEntries(
  TRACE_CHECKER_DEFINITIONS.map((definition) => [definition.type, definition]),
);

export function validateCheckerConfigs(configs: Array<Record<string, any>>, scope: string): void {
  configs.forEach((config, index) => {
    const definition = checkerDefinitions[String(config.type || '')];
    if (!definition) throw new Error(`${scope}[${index}] 的 Checker 类型无效`);
    const missing = definition.fields.filter((field) => field.required && (
      config[field.key] === undefined
      || config[field.key] === ''
      || (Array.isArray(config[field.key]) && config[field.key].length === 0)
    ));
    if (missing.length) throw new Error(`${scope}[${index}] 缺少：${missing.map((field) => field.label).join('、')}`);
  });
}

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

export function fmtPromptCacheRate(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(1)}%`
    : '暂无数据';
}

export function fmtPromptPrefixReuseRate(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(1)}%`
    : '暂无数据';
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

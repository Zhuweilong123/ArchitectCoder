import type { AgentProgressEvent, AgentReviewEvent, AgentTodoItem } from '../../services/agentChat';

export interface ChatMessage {
  id: string;
  role: 'user' | 'agent' | 'system';
  content: string;
  timestamp: number;
  steps?: AgentProgressEvent[];
  review?: AgentReviewEvent;
  // 消息类别标记：'disconnect' 用于断线提示去重（连续断线只保留一条）
  kind?: string;
}

// 持久化时裁剪 tool observation，避免撑爆 localStorage（5MB）
const OBS_LIMIT = 500;

export const clampStepForStorage = (steps: AgentProgressEvent[]): AgentProgressEvent[] =>
  steps.map((step) => ({
    ...step,
    tool_calls_detail: step.tool_calls_detail?.map((toolCall) => ({
      ...toolCall,
      observation: String(toolCall.observation).slice(0, OBS_LIMIT),
    })),
  }));

export function latestTodoState(messages: ChatMessage[]): {
  todos: AgentTodoItem[];
  planningMode: boolean;
  strategyAdvised: boolean;
} {
  for (const message of [...messages].reverse()) {
    for (const step of [...(message.steps || [])].reverse()) {
      if (Array.isArray(step.todos) && step.todos.length > 0) {
        return {
          todos: step.todos,
          planningMode: Boolean(step.planning_mode),
          strategyAdvised: Boolean(step.strategy_advised),
        };
      }
    }
  }
  return { todos: [], planningMode: false, strategyAdvised: false };
}

// 从 session_id（YYYYMMDD_HHMMSS[_suffix]）解析可读时间 "MM-DD HH:MM"
export function sessionTimeFromId(id: string): string {
  const match = id.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/);
  if (!match) return '';
  return `${match[2]}-${match[3]} ${match[4]}:${match[5]}`;
}

// 时间戳 → "MM-DD HH:MM"
export function formatTs(ms: number | null | undefined): string {
  if (!ms) return '';
  const date = new Date(ms);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// 会话主题：截断到 ~16 字
export function truncateTitle(value: string): string {
  const clean = value.replace(/\s+/g, ' ').trim();
  return clean.length > 16 ? `${clean.slice(0, 16)}…` : clean;
}

// 将后端 uml_review 的 diagram 对象归一化为 {type, name, component_id, data}
// 兼容两种形态：{type,name,data} 包裹式，或原始图对象（diagram_type/classes/... 平铺）。
export function normalizeReviewDiagrams(
  raw: any[] | null | undefined,
): Array<{ type: string; name: string; component_id: string; data: any }> {
  if (!Array.isArray(raw)) return [];
  return raw.map((diagram) => {
    const type = diagram.type || diagram.diagram_type || 'class';
    const name = diagram.name || '';
    const component_id = diagram.component_id || '';
    const data = (diagram.data && typeof diagram.data === 'object' && !Array.isArray(diagram.data))
      ? diagram.data
      : diagram;
    return { type, name, component_id, data };
  });
}

/**
 * Agent 对话 WebSocket 服务
 *
 * 与后端 /api/agent/ws/chat 端点通信，
 * 支持流式对话、进度推送、人工审核、中断。
 */

export interface AgentProgressEvent {
  event: 'progress';
  step: number;
  actions: string[];
  thought: string;
  tool_calls_detail: Array<{
    name: string;
    arguments: Record<string, unknown>;
    observation: string;
  }>;
  is_final: boolean;
  final_answer: string;
}

export interface AgentChatChunkEvent {
  event: 'chat_chunk';
  content: string;
}

export interface AgentReviewEvent {
  event: 'request_review';
  review_id: number;
  review_type: string;
  title: string;
  content: string;
  question: string;
  step: number;
}

export interface AgentDoneEvent {
  event: 'done';
  result: string;
  history?: string[];
}

export interface AgentStoppedEvent {
  event: 'stopped';
  reason: string;
}

export interface AgentErrorEvent {
  event: 'error';
  message: string;
}

export type AgentEvent =
  | AgentProgressEvent
  | AgentChatChunkEvent
  | AgentReviewEvent
  | AgentDoneEvent
  | AgentStoppedEvent
  | AgentErrorEvent;

export type AgentEventCallback = (event: AgentEvent) => void;

export function createAgentChatWs(
  onEvent: AgentEventCallback,
  token?: string,
): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';
  const wsUrl = `${protocol}//${window.location.host}/api/agent/ws/chat${tokenParam}`;
  const ws = new WebSocket(wsUrl);

  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(data as AgentEvent);
    } catch {
      console.error('[AgentChat] Failed to parse WS message:', e.data);
    }
  };

  ws.onerror = (e) => {
    console.error('[AgentChat] WebSocket error:', e);
  };

  return ws;
}

export function sendChatMessage(ws: WebSocket, message: string, opts?: {
  source_dir?: string;
  test_dir?: string;
  project_file?: string;
}) {
  ws.send(JSON.stringify({
    type: 'chat',
    message,
    source_dir: opts?.source_dir || '',
    test_dir: opts?.test_dir || '',
    project_file: opts?.project_file || '',
  }));
}

export function sendStopMessage(ws: WebSocket) {
  ws.send(JSON.stringify({ type: 'stop' }));
}

export function sendReviewResponse(ws: WebSocket, reviewId: number, response: string) {
  ws.send(JSON.stringify({
    type: 'review_response',
    review_id: reviewId,
    response,
  }));
}

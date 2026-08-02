/**
 * Agent 对话 WebSocket 服务（模块级单例）
 *
 * 与后端 /api/agent/ws/chat 端点通信，
 * 支持流式对话、进度推送、人工审核、中断、设计元素流式渲染。
 *
 * Toolbar 和 AgentChat 共享同一连接 — 通过 connectAgentChat / sendAgentMessage 操作。
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

export interface AgentDesignUpdatedEvent {
  event: 'design_updated';
  diagrams: Array<{ type: string; name: string; component_id: string; data: Record<string, unknown> }>;
  saved_to?: string;
  review?: boolean;
}

export interface AgentDesignElementEvent {
  event: 'design_element';
  type: string;
  data: string;
}

export type AgentEvent =
  | AgentProgressEvent
  | AgentChatChunkEvent
  | AgentReviewEvent
  | AgentDoneEvent
  | AgentStoppedEvent
  | AgentErrorEvent
  | AgentDesignUpdatedEvent
  | AgentDesignElementEvent;

export type AgentEventCallback = (event: AgentEvent) => void;

// ── 模块级单例 ─────────────────────────────────────────

let _ws: WebSocket | null = null;
let _onEvent: AgentEventCallback | null = null;
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null;

export function connectAgentChat(
  onEvent: AgentEventCallback,
  token?: string,
): WebSocket {
  // 如果已有连接，只更新回调（保护：不覆盖已有的真实回调为空回调）
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) {
    // 空回调是占位用的（如 Toolbar），不覆盖真实回调
    if (onEvent !== _noopEvent) {
      _onEvent = onEvent;
    }
    return _ws;
  }
  // 创建新连接
  _ws = createRawWs(onEvent, token);
  _onEvent = onEvent;
  return _ws;
}

function _noopEvent(_event: AgentEvent) { /* placeholder */ }

// ── 待发送队列（WebSocket 未 OPEN 时暂存）───────
let _pendingMessages: Array<{ message: string; opts: Record<string, unknown> }> = [];

export function sendAgentMessage(message: string, opts?: {
  source_dir?: string;
  test_dir?: string;
  project_file?: string;
  stream_mode?: boolean;
}) {
  const payload = {
    type: 'chat',
    message,
    source_dir: opts?.source_dir || '',
    test_dir: opts?.test_dir || '',
    project_file: opts?.project_file || '',
    stream_mode: opts?.stream_mode ? true : undefined,
  };

  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify(payload));
    _notifyListeners({ event: 'user_message' as any, message });
    return true;
  }

  // 等待 onopen 后发送
  if (_ws && _ws.readyState === WebSocket.CONNECTING) {
    _pendingMessages.push({ message, opts: payload });
    return false; // 消息会在 onopen 中发送
  }

  console.warn('[AgentChat] Cannot send — WebSocket not created');
  return false;
}

// ── 消息监听（供 AgentChat 注册以追加用户消息）─────────
type MessageListener = (ev: { event: string; message?: string }) => void;
let _msgListeners: MessageListener[] = [];

export function onAgentMessage(listener: MessageListener) {
  _msgListeners.push(listener);
  return () => { _msgListeners = _msgListeners.filter(l => l !== listener); };
}

function _notifyListeners(ev: { event: string; message?: string }) {
  _msgListeners.forEach(l => l(ev));
}

export function sendStopMessage() {
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({ type: 'stop' }));
  }
}

export function sendReviewResponse(reviewId: number, response: string) {
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({
      type: 'review_response',
      review_id: reviewId,
      response,
    }));
  }
}

export function disconnectAgentChat() {
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
  if (_ws) {
    _ws.close();
    _ws = null;
  }
  _onEvent = null;
}

export function isAgentConnected(): boolean {
  return _ws !== null && _ws.readyState === WebSocket.OPEN;
}

// ── 底层 WebSocket ─────────────────────────────────────

function createRawWs(onEvent: AgentEventCallback, token?: string): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';
  const wsUrl = `${protocol}//${window.location.host}/api/agent/ws/chat${tokenParam}`;
  const ws = new WebSocket(wsUrl);

  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      // 路由给当前注册的回调（连接可被 AgentChat 重新绑定）
      if (_onEvent) _onEvent(data as AgentEvent);
    } catch {
      console.error('[AgentChat] Failed to parse WS message:', e.data);
    }
  };

  ws.onopen = () => {
    // 发送连接建立前暂存的消息
    const pending = _pendingMessages.splice(0);
    for (const pm of pending) {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify(pm.opts));
        _notifyListeners({ event: 'user_message' as any, message: pm.message });
      }
    }
  };

  ws.onerror = (e) => {
    console.error('[AgentChat] WebSocket error:', e);
  };

  ws.onclose = () => {
    _ws = null;
  };

  return ws;
}

// ── 兼容旧 API（deprecated，保留过渡期）───────────────

/** @deprecated Use connectAgentChat + sendAgentMessage instead */
export function createAgentChatWs(
  onEvent: AgentEventCallback,
  token?: string,
): WebSocket {
  return connectAgentChat(onEvent, token);
}

/** @deprecated Use sendAgentMessage instead */
export function sendChatMessage(ws: WebSocket, message: string, opts?: {
  source_dir?: string;
  test_dir?: string;
  project_file?: string;
}) {
  sendAgentMessage(message, opts);
}

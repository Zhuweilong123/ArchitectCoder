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

export interface AgentUmlReviewEvent {
  event: 'uml_review';
  review_id: number;
  title: string;
  diagrams: any[];                 // 更新后的图对象列表
  original_diagrams: any[] | null; // 修改前的图对象列表（DiffViewer 对比用）
  auto?: boolean;                  // true = 框架兜底补推（Agent 漏调 submit_uml_review）
}

export interface AgentReviewTimeoutEvent {
  event: 'review_timeout';
  review_id: number;
  review_type: string;
  title: string;
  timeout: number;                 // 秒
}

export interface AgentReviewExpiredEvent {
  event: 'review_expired';
  review_id: number;               // 后端找不到该待审核请求（连接中断/会话回收/已超时）
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

export interface AgentDesignElementEvent {
  event: 'design_element';
  type: string;
  data: string;
}

export type AgentEvent =
  | AgentProgressEvent
  | AgentChatChunkEvent
  | AgentReviewEvent
  | AgentUmlReviewEvent
  | AgentReviewTimeoutEvent
  | AgentReviewExpiredEvent
  | AgentDoneEvent
  | AgentStoppedEvent
  | AgentErrorEvent
  | AgentDesignElementEvent;

export type AgentEventCallback = (event: AgentEvent) => void;

// ── 模块级单例 ─────────────────────────────────────────

let _ws: WebSocket | null = null;
let _onEvent: AgentEventCallback | null = null;
let _lastToken: string | undefined = undefined;
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let _reconnectAttempts = 0;
// 主动断开（关闭面板/切换会话）时不广播 ws_closed，避免误报"连接已断开"
let _intentionalClose = false;

export function connectAgentChat(
  onEvent: AgentEventCallback,
  token?: string,
): WebSocket {
  if (token) _lastToken = token;
  // 如果已有连接，只更新回调（保护：不覆盖已有的真实回调为空回调）
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) {
    // 空回调是占位用的（如 Toolbar），不覆盖真实回调
    if (onEvent !== _noopEvent) {
      _onEvent = onEvent;
    }
    return _ws;
  }
  // 创建新连接
  _intentionalClose = false;
  _ws = createRawWs(onEvent, _lastToken);
  _onEvent = onEvent;
  return _ws;
}

function _noopEvent(_event: AgentEvent) { /* placeholder */ }

/**
 * 确保连接存在（断线后用同一 session_id 重建）。
 * 后端 session 按 session_id 复用，重连后补发的 review_response 仍可送达
 * 阻塞中的 Agent；若审核已失效，后端会回 review_expired。
 */
function _ensureConnection(): void {
  if (!_ws || _ws.readyState === WebSocket.CLOSED) {
    _intentionalClose = false;
    _ws = createRawWs(_onEvent || _noopEvent, _lastToken);
  }
}

// ── 会话 id（localStorage 持久化，跨刷新/重开面板保持稳定）───────
// 后端据此跨 WebSocket 连接复用 agent 历史与日志文件，
// 避免刷新后丢失对话历史、或同一会话被拆成多个 trace 文件。

const SESSION_KEY = 'agentSessionId';

function _genSessionId(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}` +
    `_${Math.random().toString(36).slice(2, 6)}`;
}

function _getSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = _genSessionId();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

export function getCurrentSessionId(): string {
  return _getSessionId();
}

// ── 待发送队列（WebSocket 未 OPEN 时暂存）───────
let _pendingMessages: Array<{ message: string; opts: Record<string, unknown>; skipNotify?: boolean }> = [];

export function sendAgentMessage(message: string, opts?: {
  source_dir?: string;
  test_dir?: string;
  project_file?: string;
  stream_mode?: boolean;
  /** 跳过监听器通知 — 调用方已自行添加用户消息到 UI 时设为 true */
  skipNotify?: boolean;
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
    if (!opts?.skipNotify) {
      _notifyListeners({ event: 'user_message' as any, message });
    }
    return true;
  }

  // 连接断开/未建立：重连后暂存消息，onopen 中发送
  if (!_ws || _ws.readyState === WebSocket.CLOSED) {
    _ensureConnection();
  }
  if (_ws && _ws.readyState === WebSocket.CONNECTING) {
    _pendingMessages.push({ message, opts: payload, skipNotify: opts?.skipNotify });
    return false; // 消息会在 onopen 中发送
  }

  console.warn('[AgentChat] Cannot send — WebSocket not created');
  return false;
}

// ── 消息监听（供 AgentChat 注册以追加用户消息）─────────
type MessageListener = (ev: { event: string; message?: string; review_id?: number }) => void;
let _msgListeners: MessageListener[] = [];

export function onAgentMessage(listener: MessageListener) {
  _msgListeners.push(listener);
  return () => { _msgListeners = _msgListeners.filter(l => l !== listener); };
}

function _notifyListeners(ev: { event: string; message?: string; review_id?: number }) {
  _msgListeners.forEach(l => l(ev));
}

// ── 待审核的 UML review（AgentChat 与 DiffViewer 共享状态）───────
// uml_review 到达时由 AgentChat 登记；任一界面回复后清除。
// DiffViewer 据此决定 accept/reject 是否需同步 review_response 给阻塞中的 Agent。
let _pendingUmlReviewId: number | null = null;

export function setPendingUmlReviewId(id: number | null) {
  _pendingUmlReviewId = id;
}

export function getPendingUmlReviewId(): number | null {
  return _pendingUmlReviewId;
}

function _cleanupPendingReview(reviewId: number) {
  if (_pendingUmlReviewId === reviewId) {
    _pendingUmlReviewId = null;
  }
  _notifyListeners({ event: 'review_resolved', review_id: reviewId });
}

// 断线时暂存的 review_response，重连 onopen 后补发
let _pendingReviewPayloads: Array<{ review_id: number; payload: string }> = [];

export function sendStopMessage() {
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({ type: 'stop' }));
  }
}

/**
 * 发送审核回复。返回:
 * - 'sent'   — 已直接发送
 * - 'queued' — 连接断开，已触发重连并排队，onopen 后补发
 *              （审核若已失效，后端会回 review_expired，界面据此提示）
 * - 'failed' — 无法发送也无法重连（调用方应提示用户）
 */
export function sendReviewResponse(
  reviewId: number, response: string, decision?: string,
): 'sent' | 'queued' | 'failed' {
  const payload = JSON.stringify({
    type: 'review_response',
    review_id: reviewId,
    response,
    decision,
  });
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(payload);
    console.log('[AgentChat] review_response sent:', reviewId, decision || '(no decision)');
    _cleanupPendingReview(reviewId);
    return 'sent';
  }
  // 连接断开/未建立：重连 + 排队补发（HMR 模块重载、网络抖动场景下
  // 后端 session 仍在，Agent 可能还阻塞在审核 future 上等这个回复）
  _ensureConnection();
  if (_ws && _ws.readyState === WebSocket.CONNECTING) {
    _pendingReviewPayloads.push({ review_id: reviewId, payload });
    console.log('[AgentChat] review_response queued for reconnect:', reviewId);
    return 'queued';
  }
  console.warn('[AgentChat] review_response NOT sent — ws state:', _ws ? _ws.readyState : 'null');
  if (_pendingUmlReviewId === reviewId) {
    _pendingUmlReviewId = null; // 死掉的审核不再保留标记，避免后续界面重复尝试发送
  }
  return 'failed';
}

export function disconnectAgentChat() {
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
  _intentionalClose = true; // 主动断开不广播 ws_closed
  _pendingReviewPayloads = [];
  if (_ws) {
    _ws.close();
    _ws = null;
  }
  _onEvent = null;
  _pendingUmlReviewId = null; // 断开后审核回复无法送达，清除待审核标记
}

export function switchSession(sessionId: string): void {
  // 切到指定会话：持久化 id、丢弃待发消息、断开旧连接，下次 connect 带上新 id
  localStorage.setItem(SESSION_KEY, sessionId);
  _pendingMessages = [];
  disconnectAgentChat();
}

export function startNewSession(): void {
  // 生成新 session id 并切换
  switchSession(_genSessionId());
}

export function isAgentConnected(): boolean {
  return _ws !== null && _ws.readyState === WebSocket.OPEN;
}

// ── 底层 WebSocket ─────────────────────────────────────

function createRawWs(onEvent: AgentEventCallback, token?: string): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const params = new URLSearchParams({ session_id: _getSessionId() });
  if (token) params.set('token', token);
  const wsUrl = `${protocol}//${window.location.host}/api/agent/ws/chat?${params.toString()}`;
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
    _reconnectAttempts = 0; // 连接成功，重置退避
    // 发送连接建立前暂存的消息
    const pending = _pendingMessages.splice(0);
    for (const pm of pending) {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify(pm.opts));
        if (!pm.skipNotify) {
          _notifyListeners({ event: 'user_message' as any, message: pm.message });
        }
      }
    }
    // 补发断线期间排队的审核回复
    const queuedReviews = _pendingReviewPayloads.splice(0);
    for (const qr of queuedReviews) {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(qr.payload);
        console.log('[AgentChat] queued review_response sent after reconnect:', qr.review_id);
        _cleanupPendingReview(qr.review_id);
      }
    }
  };

  ws.onerror = (e) => {
    console.error('[AgentChat] WebSocket error:', e);
  };

  ws.onclose = (e) => {
    console.warn('[AgentChat] WebSocket closed:', e.code, e.reason || '(no reason)');
    _ws = null;
    // 排队待补发的审核回复随连接关闭而失效，通知界面
    const undelivered = _pendingReviewPayloads.splice(0);
    for (const u of undelivered) {
      if (_pendingUmlReviewId === u.review_id) {
        _pendingUmlReviewId = null;
      }
      _notifyListeners({ event: 'review_delivery_failed', review_id: u.review_id });
    }
    // 连接关闭后阻塞中的审核会被后端取消，清除待审核标记并通知界面收起审核卡
    if (_pendingUmlReviewId !== null) {
      const staleId = _pendingUmlReviewId;
      _pendingUmlReviewId = null;
      _notifyListeners({ event: 'review_resolved', review_id: staleId });
    }
    // 非主动断开（网络抖动/后端重启）→ 通知界面解除"正在执行"状态
    if (!_intentionalClose) {
      _notifyListeners({ event: 'ws_closed' });
      // 自动重连（指数退避，最多约 30s 一次）。面板常驻挂载，_onEvent 始终有效，
      // 重连后事件能正常到达界面；后端 session 按 session_id 复用。
      if (_onEvent) {
        const delay = Math.min(1000 * 2 ** _reconnectAttempts, 30000);
        _reconnectAttempts += 1;
        console.log(`[AgentChat] reconnecting in ${delay}ms (attempt ${_reconnectAttempts})`);
        _reconnectTimer = setTimeout(() => {
          _reconnectTimer = null;
          _ensureConnection();
        }, delay);
      }
    }
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

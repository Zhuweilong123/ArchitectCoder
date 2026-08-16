/**
 * AgentChat — 对话 Agent 驱动的开发对话框
 *
 * 一个可拖拽、可放大的浮动面板，通过 WebSocket 与 Agent 通信，
 * 实现：
 * - 流式聊天（chat_chunk 逐块输出，左气泡实时增长）
 * - 开发模式（工具调用步骤展示 + 审核回路）
 * - 中断控制（随时停止 Agent 执行）
 * - 左右气泡布局（用户右蓝 / AI 左白）
 * - 消息历史持久化（刷新不丢失）
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  Input, Button, message, Tag, Space, Spin, Alert, Tooltip, Collapse, Dropdown,
} from 'antd';
import {
  SendOutlined, StopOutlined, RobotOutlined,
  CheckCircleOutlined, CloseCircleOutlined,
  ToolOutlined, UserOutlined,
  ExpandOutlined, CompressOutlined, CloseOutlined, LoadingOutlined,
  PlusOutlined, HistoryOutlined,
} from '@ant-design/icons';
import { useUiStore } from '../../stores/uiStore';
import { useDiagramStore } from '../../stores/diagramStore';
import {
  connectAgentChat, sendAgentMessage, sendStopMessage, sendReviewResponse,
  disconnectAgentChat, isAgentConnected, onAgentMessage, startNewSession,
  getCurrentSessionId, switchSession,
  type AgentEvent, type AgentProgressEvent, type AgentReviewEvent,
} from '../../services/agentChat';
import { listTraces, getTraceHistory, type TraceMeta } from '../../services/api';
import { handleDesignElement, processDesignUpdated } from '../../services/designElementHandler';
import './AgentChat.css';

// ── 消息类型 ──────────────────────────────────────────

interface ChatMessage {
  id: string;
  role: 'user' | 'agent' | 'system';
  content: string;
  timestamp: number;
  steps?: AgentProgressEvent[];
  review?: AgentReviewEvent;
}

// 持久化时裁剪 tool observation，避免撑爆 localStorage（5MB）
const OBS_LIMIT = 500;
const clampStepForStorage = (steps: AgentProgressEvent[]): AgentProgressEvent[] =>
  steps.map((s) => ({
    ...s,
    tool_calls_detail: s.tool_calls_detail?.map((td) => ({
      ...td,
      observation: String(td.observation).slice(0, OBS_LIMIT),
    })),
  }));

// ── 组件 ──────────────────────────────────────────────

const AgentChat: React.FC = () => {
  const {
    agentChatVisible, setAgentChatVisible,
    agentChatExpanded, setAgentChatExpanded,
    agentChatPosition, setAgentChatPosition,
    pipelineSourceDir, pipelineTestDir,
  } = useUiStore();

  const currentFilepath = useDiagramStore((s) => s.currentFilepath);

  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    try {
      const saved = localStorage.getItem(`agentChatMessages:${getCurrentSessionId()}`);
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });
  const [inputValue, setInputValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [currentSteps, setCurrentSteps] = useState<AgentProgressEvent[]>([]);
  const [pendingReview, setPendingReview] = useState<AgentReviewEvent | null>(null);
  // 实时步骤的真相来源：WS 回调闭包可能过期，直接读写 ref 避免丢失
  const liveStepsRef = useRef<AgentProgressEvent[]>([]);

  // 流式元素的 LLM ID → 真实 ID 映射表，跨事件共享
  const idMapRef = useRef<Map<string, string>>(new Map());

  const handleDesignElementWrapper = useCallback((event: { type: string; data: string }) => {
    handleDesignElement(useDiagramStore.getState(), event, idMapRef.current);
  }, []);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const dragOffsetRef = useRef<{ dx: number; dy: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  // ── 拖拽（header 拖动整个面板） ──
  const handleHeaderMouseDown = useCallback((e: React.MouseEvent) => {
    // 点击 header 右侧按钮（放大/关闭）时不触发拖拽
    if ((e.target as HTMLElement).closest('.agent-chat-header-right')) return;
    if (e.button !== 0) return;

    const panel = (e.currentTarget as HTMLElement).closest('.agent-chat-panel');
    if (!panel) return;
    const rect = panel.getBoundingClientRect();

    dragOffsetRef.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    setDragging(true);

    const handleMove = (moveEvent: MouseEvent) => {
      if (!dragOffsetRef.current) return;
      const x = Math.max(0, Math.min(window.innerWidth - rect.width, moveEvent.clientX - dragOffsetRef.current.dx));
      const y = Math.max(0, Math.min(window.innerHeight - 40, moveEvent.clientY - dragOffsetRef.current.dy));
      setAgentChatPosition({ x, y });
    };
    const handleUp = () => {
      dragOffsetRef.current = null;
      setDragging(false);
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
    };

    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
  }, [setAgentChatPosition]);

  // ── 持久化消息（流式中跳过；工具观察结果裁剪后存储） ──
  useEffect(() => {
    try {
      const hasStreaming = messages.some((m) => m.id.startsWith('stream_'));
      if (!hasStreaming) {
        const toSave = messages.slice(-100).map((m) =>
          m.steps ? { ...m, steps: clampStepForStorage(m.steps) } : m,
        );
        localStorage.setItem(`agentChatMessages:${getCurrentSessionId()}`, JSON.stringify(toSave));
      }
    } catch { /* ignore */ }
  }, [messages]);

  // ── 自动滚动（流式时用 auto 避免 smooth 抖动） ──
  useEffect(() => {
    const el = messagesEndRef.current;
    if (!el) return;
    el.scrollIntoView({ behavior: busy ? 'auto' : 'smooth' });
  }, [messages, currentSteps, busy]);

  // ── 连接 WebSocket ──
  const connect = useCallback(() => {
    const token = (import.meta as any).env?.VITE_API_TOKEN as string | undefined;
    const ws = connectAgentChat((event: AgentEvent) => {
      switch (event.event) {
        // ── 聊天流式 ──
        case 'chat_chunk': {
          setMessages((prev) => {
            const lastIdx = prev.length - 1;
            const lastMsg = prev[lastIdx];
            if (lastMsg && lastMsg.role === 'agent' && lastMsg.id.startsWith('stream_')) {
              const copy = [...prev];
              copy[lastIdx] = { ...lastMsg, content: lastMsg.content + event.content };
              return copy;
            }
            return [...prev, {
              id: `stream_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
              role: 'agent' as const,
              content: event.content,
              timestamp: Date.now(),
            }];
          });
          break;
        }

        // ── 开发进度 ──
        case 'progress': {
          liveStepsRef.current = [
            ...liveStepsRef.current.filter((s) => s.step !== event.step),
            event,
          ].sort((a, b) => a.step - b.step);
          // 只同步最新一步触发渲染，避免每步全量 setState
          setCurrentSteps([...liveStepsRef.current]);
          break;
        }

        // ── 审核请求 ──
        case 'request_review': {
          setPendingReview(event);
          setMessages((prev) => [
            ...prev,
            {
              id: `review_${Date.now()}`,
              role: 'system',
              content: `🔔 Agent 请求审核 [${event.review_type}]: ${event.title}\n\n${event.content}\n\n❓ ${event.question}`,
              timestamp: Date.now(),
              review: event,
            },
          ]);
          break;
        }

        // ── 完成 ──
        case 'done': {
          setBusy(false);
          const steps = liveStepsRef.current;
          liveStepsRef.current = [];
          setCurrentSteps([]);
          setMessages((prev) => {
            const hasStream = prev.some((m) => m.id.startsWith('stream_'));
            if (hasStream) {
              // finalize 流式消息，不追加重复内容
              return prev.map((m) =>
                m.id.startsWith('stream_')
                  ? {
                      ...m,
                      id: m.id.replace('stream_', 'agent_'),
                      content: event.result || m.content,
                      steps: steps.length ? steps : undefined,
                    }
                  : m,
              );
            }
            // 无流式消息（如 SSE 直出结果），追加新消息
            return [
              ...prev,
              {
                id: `agent_${Date.now()}`,
                role: 'agent' as const,
                content: event.result || '(空回复)',
                timestamp: Date.now(),
                steps: steps.length ? steps : undefined,
              },
            ];
          });
          break;
        }

        // ── 设计更新（optimize_uml 修改后透传）──
        case 'design_updated': {
          const diagrams = event.diagrams;
          if (Array.isArray(diagrams) && diagrams.length > 0) {
            processDesignUpdated(diagrams, [], useUiStore.getState(), useDiagramStore.getState());
          }
          break;
        }

        // ── 流式元素（optimize_uml 流式模式逐元素渲染）──
        case 'design_element': {
          handleDesignElementWrapper(event);
          break;
        }

        // ── 停止 ──
        case 'stopped': {
          setBusy(false);
          liveStepsRef.current = [];
          setCurrentSteps([]);
          // 聊天模式下把流式消息 finalize
          setMessages((prev) =>
            prev.map((m) =>
              m.id.startsWith('stream_')
                ? { ...m, id: m.id.replace('stream_', 'agent_') }
                : m,
            ),
          );
          setMessages((prev) => [
            ...prev,
            {
              id: `system_${Date.now()}`,
              role: 'system',
              content: `⏹️ ${event.reason}`,
              timestamp: Date.now(),
            },
          ]);
          break;
        }

        // ── 错误 ──
        case 'error': {
          setBusy(false);
          liveStepsRef.current = [];
          setCurrentSteps([]);
          // 聊天流式半途断掉，finalize 已收到的部分
          setMessages((prev) =>
            prev.map((m) =>
              m.id.startsWith('stream_')
                ? { ...m, id: m.id.replace('stream_', 'agent_') }
                : m,
            ),
          );
          setMessages((prev) => [
            ...prev,
            {
              id: `error_${Date.now()}`,
              role: 'system',
              content: `❌ ${event.message}`,
              timestamp: Date.now(),
            },
          ]);
          break;
        }
      }
    }, token);

    return ws;
  }, []);

  // ── 发送消息 ──
  const handleSend = useCallback(() => {
    const text = inputValue.trim();
    if (!text || busy) return;

    connect();
    sendAgentMessage(text, {
      source_dir: pipelineSourceDir,
      test_dir: pipelineTestDir,
      project_file: currentFilepath || '',
      skipNotify: true,
    });

    setMessages((prev) => [
      ...prev,
      {
        id: `user_${Date.now()}`,
        role: 'user' as const,
        content: text,
        timestamp: Date.now(),
      },
    ]);
    setInputValue('');
    setBusy(true);
    liveStepsRef.current = [];
    setCurrentSteps([]);
    setPendingReview(null);
  }, [inputValue, busy, connect, pipelineSourceDir, pipelineTestDir, currentFilepath]);

  // ── 中断 ──
  const handleStop = useCallback(() => {
    sendStopMessage();
    // 后端会发 stopped 事件，由回调处理状态更新
  }, []);

  // ── 审核回复 ──
  const handleReviewResponse = useCallback((reviewId: number, response: string) => {
    sendReviewResponse(reviewId, response);
    setPendingReview(null);
    setMessages((prev) => [
      ...prev,
      {
        id: `review_resp_${Date.now()}`,
        role: 'system',
        content: `✅ 审核回复: ${response}`,
        timestamp: Date.now(),
      },
    ]);
  }, []);

  // ── 键盘快捷键 ──
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  // ── 清理 ──
  const handleClose = useCallback(() => {
    if (busy) handleStop();
    disconnectAgentChat();
    setAgentChatVisible(false);
  }, [busy, handleStop, setAgentChatVisible]);

  // ── 新对话（新 session）──
  const handleNewSession = useCallback(() => {
    startNewSession();  // 生成新 id + 断开
    setMessages([]);
    liveStepsRef.current = [];
    setCurrentSteps([]);
    setPendingReview(null);
    setInputValue('');
    setBusy(false);
    connect();
  }, [connect]);

  // ── 历史会话（恢复继续聊，结论级）──
  const [sessions, setSessions] = useState<TraceMeta[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  const loadSessions = useCallback(async () => {
    setSessionsLoading(true);
    try {
      setSessions(await listTraces());
    } catch {
      // 后端未启动等，忽略
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  const handleResumeSession = useCallback(async (targetId: string) => {
    if (targetId === getCurrentSessionId()) return;
    setSessionsLoading(true);
    try {
      const history = await getTraceHistory(targetId);
      switchSession(targetId);
      setMessages(history.map((h, i) => ({
        id: `resume_${Date.now()}_${i}`,
        role: (h.role === 'user' ? 'user' : 'agent') as 'user' | 'agent',
        content: h.content,
        timestamp: Date.now(),
      })));
      liveStepsRef.current = [];
      setCurrentSteps([]);
      setPendingReview(null);
      setInputValue('');
      setBusy(false);
      connect();
    } catch {
      message.error('恢复会话失败');
    } finally {
      setSessionsLoading(false);
    }
  }, [connect]);

  // ── 监听外部消息（Toolbar 等通过 sendAgentMessage 发送）──
  useEffect(() => {
    return onAgentMessage((ev) => {
      if (ev.event === 'user_message' && ev.message) {
        setMessages((prev) => [
          ...prev,
          {
            id: `user_${Date.now()}`,
            role: 'user' as const,
            content: ev.message || '',
            timestamp: Date.now(),
          },
        ]);
        setBusy(true);
        liveStepsRef.current = [];
        setCurrentSteps([]);
        setPendingReview(null);
      }
    });
  }, []);

  const toggleExpand = useCallback(() => {
    const expanding = !agentChatExpanded;
    const curW = panelRef.current?.offsetWidth || (agentChatExpanded ? 520 : 420);
    const nextW = expanding ? 520 : 420;
    const nextH = expanding ? window.innerHeight - 60 : 520;

    // 保持右边缘对齐：展开时若右边缘超出可视区则左移
    const rightEdge = Math.min(agentChatPosition.x + curW, window.innerWidth - 8);
    const x = Math.max(0, rightEdge - nextW);
    const y = Math.max(0, Math.min(agentChatPosition.y, window.innerHeight - nextH));

    setAgentChatExpanded(expanding);
    setAgentChatPosition({ x, y });
  }, [agentChatExpanded, setAgentChatPosition, agentChatPosition]);

  // ── 渲染工具调用步骤 ──
  const renderSteps = (steps: AgentProgressEvent[], collapsible: boolean) => {
    if (!steps.length) return null;

    const panelHeader = (
      <span className="agent-steps-header-label">
        <ToolOutlined /> 工具调用 {steps.length} 步
      </span>
    );

    const body = (
      <div className="agent-steps">
        {steps.map((s) => (
          <div key={s.step} className="agent-step">
            <div className="agent-step-header">
              <Tag color="blue">Step {s.step}</Tag>
              {s.actions.map((a) => (
                <Tag key={a} icon={<ToolOutlined />} color="processing">{a}</Tag>
              ))}
              {s.is_final && <Tag color="success">完成</Tag>}
            </div>
            {s.thought && (
              <div className="agent-step-thought">
                <span className="agent-step-label">推理</span>
                {s.thought}
              </div>
            )}
            {s.tool_calls_detail?.map((td, i) => (
              <div key={i} className="agent-tool-call">
                <div className="agent-tool-name">
                  <ToolOutlined /> {td.name}
                  {td.arguments && Object.keys(td.arguments).length > 0 && (
                    <span className="agent-tool-args">
                      ({JSON.stringify(td.arguments).slice(0, 120)})
                    </span>
                  )}
                </div>
                {td.observation && (
                  <pre className="agent-tool-obs">{String(td.observation).slice(0, 500)}</pre>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
    );

    // 执行中或已有步骤的实时区域 → 直接展示；收进消息的 → 可折叠
    return collapsible ? (
      <Collapse
        ghost
        size="small"
        className="agent-steps-collapse"
        defaultActiveKey={[]}
        items={[{ key: 'steps', label: panelHeader, children: body }]}
      />
    ) : (
      body
    );
  };

  // ── 主渲染 ──
  return (
    <>
      {/* 聊天按钮 */}
      <Tooltip title="AI 开发助手">
        <Button
          type="primary"
          shape="circle"
          size="large"
          icon={<RobotOutlined />}
          onClick={() => setAgentChatVisible(true)}
          className="agent-chat-trigger"
          style={{
            position: 'fixed',
            bottom: 24,
            right: 24,
            zIndex: 1000,
            width: 52,
            height: 52,
            boxShadow: '0 4px 14px rgba(24,144,255,0.4)',
            display: agentChatVisible ? 'none' : 'flex',
          }}
        />
      </Tooltip>

      {/* 对话面板 */}
      {agentChatVisible && (
        <div
          ref={panelRef}
          className={`agent-chat-panel ${agentChatExpanded ? 'expanded' : 'collapsed'}`}
          style={{ left: agentChatPosition.x, top: agentChatPosition.y }}
        >
          {/* Header */}
          <div
            className={`agent-chat-header${dragging ? ' dragging' : ''}`}
            onMouseDown={handleHeaderMouseDown}
          >
            <div className="agent-chat-header-left">
              <RobotOutlined style={{ marginRight: 8 }} />
              <span>AI 开发助手</span>
              {busy && <LoadingOutlined style={{ marginLeft: 8 }} spin />}
            </div>
            <div className="agent-chat-header-right">
              <Dropdown
                menu={{
                  items: sessionsLoading && sessions.length === 0
                    ? [{ key: '__loading__', label: '加载中...', disabled: true }]
                    : sessions.slice(0, 20).map((s) => ({
                        key: s.session_id,
                        label: (
                          <span style={{ fontSize: 12 }}>
                            {s.session_id}
                            {s.first_ts_ms ? ` · ${new Date(s.first_ts_ms).toLocaleString()}` : ''}
                          </span>
                        ),
                      })),
                  onClick: ({ key }) => handleResumeSession(key),
                }}
                onOpenChange={(open) => { if (open) loadSessions(); }}
                trigger={['click']}
              >
                <Button
                  type="text"
                  size="small"
                  icon={<HistoryOutlined />}
                  disabled={busy || sessionsLoading}
                >
                  历史会话
                </Button>
              </Dropdown>
              <Tooltip title="新对话（新 session）">
                <Button
                  type="text"
                  size="small"
                  icon={<PlusOutlined />}
                  onClick={handleNewSession}
                  disabled={busy}
                >
                  新对话
                </Button>
              </Tooltip>
              <Tooltip title={agentChatExpanded ? '缩小' : '放大'}>
                <Button
                  type="text"
                  size="small"
                  icon={agentChatExpanded ? <CompressOutlined /> : <ExpandOutlined />}
                  onClick={toggleExpand}
                />
              </Tooltip>
              <Tooltip title="关闭">
                <Button
                  type="text"
                  size="small"
                  icon={<CloseOutlined />}
                  onClick={handleClose}
                />
              </Tooltip>
            </div>
          </div>

          {/* Messages */}
          <div className="agent-chat-messages">
            {messages.length === 0 && !busy && (
              <div className="agent-chat-empty">
                <RobotOutlined style={{ fontSize: 32, color: '#bbb', marginBottom: 12 }} />
                <p>👋 我是 AI 开发助手</p>
                <p className="agent-chat-hint">
                  我可以帮你：设计 UML、生成代码、验证代码、编写测试、修复 bug
                </p>
                <div className="agent-chat-examples">
                  <Button size="small" onClick={() => setInputValue('创建一个计算器系统，支持加减乘除')}>
                    创建计算器
                  </Button>
                  <Button size="small" onClick={() => setInputValue('设计一个用户认证系统，包含注册、登录、密码重置')}>
                    用户认证系统
                  </Button>
                </div>
              </div>
            )}

            {messages.map((msg) => (
              <div key={msg.id} className={`agent-message agent-message-${msg.role}${msg.id.startsWith('stream_') ? ' agent-message-streaming' : ''}`}>
                <div className="agent-message-avatar-wrap">
                  <div className="agent-message-avatar">
                    {msg.role === 'user' ? <UserOutlined /> : msg.role === 'agent' ? <RobotOutlined /> : null}
                  </div>
                </div>
                <div className="agent-message-body">
                  <div className="agent-message-content">
                    {msg.content.split('\n').map((line, i) => (
                      <span key={i}>{line}<br /></span>
                    ))}
                  </div>
                  {msg.steps && renderSteps(msg.steps, true)}
                </div>
              </div>
            ))}

            {/* 实时步骤（开发模式） */}
            {busy && currentSteps.length > 0 && (
              <div className="agent-message agent-message-agent">
                <div className="agent-message-avatar-wrap">
                  <div className="agent-message-avatar"><RobotOutlined /></div>
                </div>
                <div className="agent-message-body">
                  <Spin size="small" style={{ marginRight: 8 }} />
                  <span style={{ color: '#888' }}>正在执行...</span>
                  {renderSteps(currentSteps, false)}
                </div>
              </div>
            )}

            {/* 审核请求 */}
            {pendingReview && (
              <div className="agent-review-card">
                <Alert
                  type="warning"
                  message={`🔔 审核请求 — ${pendingReview.review_type}`}
                  description={
                    <div>
                      <p><strong>{pendingReview.title}</strong></p>
                      <pre style={{ maxHeight: 150, overflow: 'auto', fontSize: 12 }}>
                        {pendingReview.content.slice(0, 1000)}
                      </pre>
                      <p style={{ marginTop: 8 }}>❓ {pendingReview.question}</p>
                      <Space style={{ marginTop: 8 }}>
                        <Button
                          type="primary"
                          size="small"
                          icon={<CheckCircleOutlined />}
                          onClick={() => handleReviewResponse(pendingReview.review_id, '批准，继续')}
                        >
                          批准
                        </Button>
                        <Button
                          size="small"
                          icon={<CloseCircleOutlined />}
                          onClick={() => handleReviewResponse(pendingReview.review_id, '拒绝，请修改')}
                        >
                          拒绝
                        </Button>
                        <Button
                          size="small"
                          onClick={() => handleReviewResponse(pendingReview.review_id, '查看后再说')}
                        >
                          稍后
                        </Button>
                      </Space>
                    </div>
                  }
                />
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <div className="agent-chat-input">
            <Input.TextArea
              ref={inputRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入开发需求..."
              autoSize={{ minRows: 1, maxRows: 4 }}
              disabled={busy}
              style={{ resize: 'none' }}
            />
            <div className="agent-chat-input-actions">
              {busy ? (
                <Button
                  danger
                  icon={<StopOutlined />}
                  onClick={handleStop}
                  size="small"
                >
                  停止
                </Button>
              ) : (
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  onClick={handleSend}
                  disabled={!inputValue.trim()}
                  size="small"
                >
                  发送
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default AgentChat;

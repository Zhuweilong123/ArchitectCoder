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
  Input, Button, message, Tag, Space, Spin, Alert, Tooltip,
} from 'antd';
import {
  SendOutlined, StopOutlined, RobotOutlined,
  CheckCircleOutlined, CloseCircleOutlined,
  ToolOutlined, UserOutlined,
  ExpandOutlined, CompressOutlined, CloseOutlined, LoadingOutlined,
} from '@ant-design/icons';
import { useUiStore } from '../../stores/uiStore';
import { useDiagramStore } from '../../stores/diagramStore';
import {
  createAgentChatWs, sendChatMessage, sendStopMessage, sendReviewResponse,
  type AgentEvent, type AgentProgressEvent, type AgentReviewEvent,
} from '../../services/agentChat';
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

// ── 组件 ──────────────────────────────────────────────

const AgentChat: React.FC = () => {
  const {
    agentChatVisible, setAgentChatVisible,
    agentChatExpanded, setAgentChatExpanded,
    pipelineSourceDir, pipelineTestDir,
  } = useUiStore();

  const currentFilepath = useDiagramStore((s) => s.currentFilepath);

  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    try {
      const saved = localStorage.getItem('agentChatMessages');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });
  const [inputValue, setInputValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [currentSteps, setCurrentSteps] = useState<AgentProgressEvent[]>([]);
  const [pendingReview, setPendingReview] = useState<AgentReviewEvent | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);
  const modeRef = useRef<'chat' | 'dev'>('chat');

  // ── 持久化消息（流式中跳过） ──
  useEffect(() => {
    try {
      const hasStreaming = messages.some((m) => m.id.startsWith('stream_'));
      if (!hasStreaming) {
        localStorage.setItem('agentChatMessages', JSON.stringify(messages.slice(-100)));
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
    if (wsRef.current?.readyState === WebSocket.OPEN) return wsRef.current;

    const token = (import.meta as any).env?.VITE_API_TOKEN as string | undefined;
    const ws = createAgentChatWs((event: AgentEvent) => {
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
          modeRef.current = 'dev';
          setCurrentSteps((prev) => {
            const existing = prev.findIndex((s) => s.step === event.step);
            if (existing >= 0) {
              const copy = [...prev];
              copy[existing] = event;
              return copy;
            }
            return [...prev, event];
          });
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
          if (modeRef.current === 'chat') {
            // 聊天模式：stream_ → agent_，或回退补一个消息
            setMessages((prev) => {
              const hasStreaming = prev.some((m) => m.id.startsWith('stream_'));
              if (hasStreaming) {
                return prev.map((m) =>
                  m.id.startsWith('stream_')
                    ? { ...m, id: m.id.replace('stream_', 'agent_') }
                    : m,
                );
              }
              // 回退：流式期间没创建消息（空响应等）
              if (event.result) {
                return [...prev, {
                  id: `agent_${Date.now()}`,
                  role: 'agent',
                  content: event.result,
                  timestamp: Date.now(),
                }];
              }
              return prev;
            });
          } else {
            // 开发模式：追加完整结果
            const steps = [...currentSteps];
            setCurrentSteps([]);
            if (event.result) {
              setMessages((prev) => [
                ...prev,
                {
                  id: `agent_${Date.now()}`,
                  role: 'agent',
                  content: event.result,
                  timestamp: Date.now(),
                  steps,
                },
              ]);
            }
          }
          break;
        }

        // ── 停止 ──
        case 'stopped': {
          setBusy(false);
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

    wsRef.current = ws;
    return ws;
  }, [currentSteps]);

  // ── 发送消息 ──
  const handleSend = useCallback(() => {
    const text = inputValue.trim();
    if (!text || busy) return;

    const ws = connect();
    const doSend = () => {
      sendChatMessage(ws, text, {
        source_dir: pipelineSourceDir,
        test_dir: pipelineTestDir,
        project_file: currentFilepath || '',
      });
    };

    if (ws.readyState === WebSocket.OPEN) {
      doSend();
    } else {
      ws.onopen = () => doSend();
    }

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
    setCurrentSteps([]);
    setPendingReview(null);
    modeRef.current = 'chat';  // 乐观默认聊天，progress 事件会纠正
  }, [inputValue, busy, connect, pipelineSourceDir, pipelineTestDir, currentFilepath]);

  // ── 中断 ──
  const handleStop = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      sendStopMessage(wsRef.current);
    }
    // 后端会发 stopped 事件，由回调处理状态更新
  }, []);

  // ── 审核回复 ──
  const handleReviewResponse = useCallback((reviewId: number, response: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      sendReviewResponse(wsRef.current, reviewId, response);
    }
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
    wsRef.current?.close();
    wsRef.current = null;
    setAgentChatVisible(false);
  }, [busy, handleStop, setAgentChatVisible]);

  const toggleExpand = useCallback(() => {
    setAgentChatExpanded(!agentChatExpanded);
  }, [agentChatExpanded, setAgentChatExpanded]);

  // ── 渲染工具调用步骤 ──
  const renderSteps = (steps: AgentProgressEvent[]) => {
    if (!steps.length) return null;
    return (
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
        <div className={`agent-chat-panel ${agentChatExpanded ? 'expanded' : 'collapsed'}`}>
          {/* Header */}
          <div className="agent-chat-header">
            <div className="agent-chat-header-left">
              <RobotOutlined style={{ marginRight: 8 }} />
              <span>AI 开发助手</span>
              {busy && <LoadingOutlined style={{ marginLeft: 8 }} spin />}
            </div>
            <div className="agent-chat-header-right">
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
                <div className="agent-message-avatar">
                  {msg.role === 'user' ? <UserOutlined /> : msg.role === 'agent' ? <RobotOutlined /> : null}
                </div>
                <div className="agent-message-body">
                  <div className="agent-message-content">
                    {msg.content.split('\n').map((line, i) => (
                      <span key={i}>{line}<br /></span>
                    ))}
                  </div>
                  {msg.steps && renderSteps(msg.steps)}
                </div>
              </div>
            ))}

            {/* 实时步骤（开发模式） */}
            {busy && currentSteps.length > 0 && (
              <div className="agent-message agent-message-agent">
                <div className="agent-message-avatar"><RobotOutlined /></div>
                <div className="agent-message-body">
                  <Spin size="small" style={{ marginRight: 8 }} />
                  <span style={{ color: '#888' }}>正在执行...</span>
                  {renderSteps(currentSteps)}
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
              <Button
                size="small"
                onClick={() => {
                  setMessages([]);
                  localStorage.removeItem('agentChatMessages');
                }}
                disabled={busy}
              >
                清空
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default AgentChat;

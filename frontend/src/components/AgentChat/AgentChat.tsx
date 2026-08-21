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
  PlusOutlined, HistoryOutlined, SwapOutlined,
} from '@ant-design/icons';
import { useUiStore } from '../../stores/uiStore';
import { useDiagramStore } from '../../stores/diagramStore';
import {
  connectAgentChat, sendAgentMessage, sendStopMessage,
  isAgentConnected, onAgentMessage, startNewSession,
  getCurrentSessionId, switchSession,
  type AgentEvent, type AgentProgressEvent, type AgentReviewEvent,
} from '../../services/agentChat';
import { listTraces, getTraceHistory, type TraceMeta } from '../../services/api';
import { handleDesignElement, processDesignUpdated } from '../../services/designElementHandler';
import { useReviewStore } from '../../stores/reviewStore';
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

// ── 会话标识 ──────────────────────────────────────────

// 从 session_id（YYYYMMDD_HHMMSS[_suffix]）解析可读时间 "MM-DD HH:MM"
function sessionTimeFromId(id: string): string {
  const m = id.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/);
  if (!m) return '';
  return `${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
}

// 时间戳 → "MM-DD HH:MM"
function formatTs(ms: number | null | undefined): string {
  if (!ms) return '';
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 会话主题：截断到 ~16 字
function truncateTitle(s: string): string {
  const clean = s.replace(/\s+/g, ' ').trim();
  return clean.length > 16 ? clean.slice(0, 16) + '…' : clean;
}

// 将后端 uml_review 的 diagram 对象归一化为 {type, name, component_id, data}
// 兼容两种形态：{type,name,data} 包裹式，或原始图对象（diagram_type/classes/... 平铺）。
function normalizeReviewDiagrams(
  raw: any[] | null | undefined,
): Array<{ type: string; name: string; component_id: string; data: any }> {
  if (!Array.isArray(raw)) return [];
  return raw.map((d) => {
    const type = d.type || d.diagram_type || 'class';
    const name = d.name || '';
    const component_id = d.component_id || '';
    const data = (d.data && typeof d.data === 'object' && !Array.isArray(d.data))
      ? d.data
      : d;
    return { type, name, component_id, data };
  });
}

// ── 组件 ──────────────────────────────────────────────

const AgentChat: React.FC = () => {
  const {
    agentChatVisible, setAgentChatVisible,
    agentChatExpanded, setAgentChatExpanded,
    agentChatPosition, setAgentChatPosition,
    sourceDir, testDir,
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
  // 审核状态来自共享 reviewStore（与 DiffViewer 联动，单一事实源）
  const review = useReviewStore();
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

        // ── 审核请求（敏感命令批准等，复用通用审核卡）──
        case 'request_review': {
          const isBash = event.review_type === 'bash_command';
          useReviewStore.getState().showReview({
            reviewId: event.review_id,
            reviewType: event.review_type,
            title: event.title,
            content: event.content,
            question: event.question,
          });
          setMessages((prev) => [
            ...prev,
            {
              id: `review_${Date.now()}`,
              role: 'system',
              content: isBash
                ? `🛡️ Agent 请求执行敏感命令:\n\n${event.content}\n\n❓ ${event.question}`
                : `🔔 Agent 请求审核 [${event.review_type}]: ${event.title}\n\n${event.content}\n\n❓ ${event.question}`,
              timestamp: Date.now(),
              review: event,
            },
          ]);
          break;
        }

        // ── UML diff 审核（submit_uml_review / 框架兜底补推）──
        case 'uml_review': {
          const diagrams = normalizeReviewDiagrams(event.diagrams);
          // 用原始图列表构造快照，DiffViewer 据此生成 before/after 对比
          const snapshot: Record<string, any> = {};
          for (const spec of normalizeReviewDiagrams(event.original_diagrams)) {
            snapshot[`${spec.type}:${spec.name || ''}`] = spec.data;
          }
          processDesignUpdated(
            diagrams, [], useUiStore.getState(), useDiagramStore.getState(), snapshot,
          );
          // 登记共享审核状态，DiffViewer 与聊天审核卡据此联动
          useReviewStore.getState().showReview({
            reviewId: event.review_id,
            reviewType: 'uml_diff',
            title: event.title,
            question: '是否接受此变更？',
          });
          setMessages((prev) => [
            ...prev,
            {
              id: `review_${Date.now()}`,
              role: 'system',
              content: event.auto
                ? `🛡️ ${event.title}\n\nAgent 修改了设计文件但未主动提交审核，框架已自动补推。请在右侧「差异对比」面板查看变更，并确认是否接受。`
                : `🔔 Agent 请求 UML 设计审核: ${event.title}\n\n请在右侧「差异对比」面板查看变更，并确认是否接受。`,
              timestamp: Date.now(),
            },
          ]);
          break;
        }

        // ── 审核超时（Agent 已继续自行推进）──
        case 'review_timeout': {
          useReviewStore.getState().expire(
            `审核超时（${Math.round(event.timeout)}s 未响应），Agent 已继续执行`,
          );
          setMessages((prev) => [
            ...prev,
            {
              id: `review_timeout_${Date.now()}`,
              role: 'system',
              content: `⏰ 审核「${event.title}」超时（${Math.round(event.timeout)}s 未响应），Agent 已继续执行。如需检查变更，请查看右侧「差异对比」面板。`,
              timestamp: Date.now(),
            },
          ]);
          break;
        }

        // ── 审核已失效（重连补发后后端找不到该待审核请求）──
        case 'review_expired': {
          useReviewStore.getState().expire('连接中断期间后端已取消该任务');
          setBusy(false);
          setMessages((prev) => [
            ...prev,
            {
              id: `review_expired_${Date.now()}`,
              role: 'system',
              content: '⚠️ 审核已失效（连接中断期间后端已取消该任务）。请重新发起请求。',
              timestamp: Date.now(),
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
          // 任务中断时挂起的审核已无人消费，置为失效
          useReviewStore.getState().expire('任务已停止，审核随之失效');
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
          // 任务出错时挂起的审核已无人消费，置为失效
          useReviewStore.getState().expire('任务出错，审核随之失效');
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
      source_dir: sourceDir,
      test_dir: testDir,
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
  }, [inputValue, busy, connect, sourceDir, testDir, currentFilepath]);

  // ── 中断 ──
  const handleStop = useCallback(() => {
    sendStopMessage();
    // 后端会发 stopped 事件，由回调处理状态更新
  }, []);

  // ── 审核状态迁移记录（pending → accepted/rejected 时在消息流留痕）──
  const reviewStatus = review.status;
  const reviewActedFrom = review.actedFrom;
  const prevReviewStatusRef = useRef(reviewStatus);
  useEffect(() => {
    const prev = prevReviewStatusRef.current;
    prevReviewStatusRef.current = reviewStatus;
    if (prev === 'pending' && (reviewStatus === 'accepted' || reviewStatus === 'rejected')) {
      const fromText = reviewActedFrom === 'diff' ? '（在 DiffViewer 操作）' : '';
      setMessages((prevMsgs) => [
        ...prevMsgs,
        {
          id: `review_done_${Date.now()}`,
          role: 'system',
          content: reviewStatus === 'accepted'
            ? `✅ 已批准审核${fromText}`
            : `🚫 已拒绝审核${fromText}，Agent 将根据反馈修订`,
          timestamp: Date.now(),
        },
      ]);
    }
  }, [reviewStatus, reviewActedFrom]);

  // ── 键盘快捷键 ──
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  // ── 关闭面板（仅隐藏）──
  // 连接与 agent 运行都和面板开关解耦：关面板只是收起 UI，
  // agent 在后台继续跑，重开面板可继续查看进度或响应审核。
  // 停止 agent 用面板里的「停止」按钮；断开连接只发生在新对话/切换会话。
  const handleClose = useCallback(() => {
    setAgentChatVisible(false);
  }, [setAgentChatVisible]);

  // ── 新对话（新 session）──
  const handleNewSession = useCallback(() => {
    startNewSession();  // 生成新 id + 断开
    setMessages([]);
    liveStepsRef.current = [];
    setCurrentSteps([]);
    useReviewStore.getState().clear();
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
      useReviewStore.getState().clear();
      setInputValue('');
      setBusy(false);
      connect();
    } catch {
      message.error('恢复会话失败');
    } finally {
      setSessionsLoading(false);
    }
  }, [connect]);

  // ── 挂载即建立长连接：连接随应用存活，与面板开关解耦 ──
  // AgentChat 在 App 中常驻挂载（App.tsx），面板只是显示/隐藏；
  // 连接一旦建立就不因关面板而断开，agent 在后台持续运行。
  useEffect(() => {
    connect();
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
      } else if (ev.event === 'review_delivery_failed') {
        // 重连补发失败（如后端不可达）：审核置为失效并提示
        useReviewStore.getState().expire('审核回复未能送达后端');
        setMessages((prev) => [
          ...prev,
          {
            id: `review_fail_${Date.now()}`,
            role: 'system',
            content: '❌ 审核回复未能送达后端（连接失败）。请确认后端已启动后重试。',
            timestamp: Date.now(),
          },
        ]);
      } else if (ev.event === 'ws_closed') {
        // 非主动断开：后端不会再推送 done/error，解除"正在执行"避免永久卡住；
        // 挂起的审核随连接中断失效（后端断连时已取消任务）
        setBusy(false);
        liveStepsRef.current = [];
        setCurrentSteps([]);
        useReviewStore.getState().expire('连接中断，审核随之失效');
        setMessages((prev) => [
          ...prev,
          {
            id: `ws_closed_${Date.now()}`,
            role: 'system',
            content: '🔌 与 AI 助手的连接已断开，当前任务已中断。重新发送消息会自动重连。',
            timestamp: Date.now(),
          },
        ]);
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
              {s.actions.map((a, ai) => (
                <Tag key={`${ai}_${a}`} icon={<ToolOutlined />} color="processing">{a}</Tag>
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

  // ── 当前会话标识（底部状态栏）──
  const currentSessionId = getCurrentSessionId();
  const firstUserMsg = messages.find((m) => m.role === 'user');
  const sessionTitle = firstUserMsg ? truncateTitle(firstUserMsg.content) : '';
  const sessionTime = sessionTimeFromId(currentSessionId);

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
                        icon: s.session_id === currentSessionId
                          ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                          : undefined,
                        disabled: s.session_id === currentSessionId,
                        label: (
                          <span style={{ fontSize: 12 }}>
                            {s.title ? `${truncateTitle(s.title)} · ` : ''}
                            {formatTs(s.first_ts_ms) || s.session_id}
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

            {/* 审核请求（与 DiffViewer 联动，状态来自 reviewStore） */}
            {review.status === 'pending' && !review.deferred && (
              <div className="agent-review-card">
                {review.reviewType === 'uml_diff' ? (
                  <Alert
                    type="warning"
                    message="🔔 Agent 请求设计审核"
                    description={
                      <div>
                        <p style={{ margin: 0 }}><strong>{review.title}</strong></p>
                        <p style={{ margin: '4px 0 0', fontSize: 12, color: '#888' }}>
                          变更详情见右侧「差异对比」面板
                        </p>
                        <Space style={{ marginTop: 8 }}>
                          <Button
                            type="primary"
                            size="small"
                            icon={<SwapOutlined />}
                            onClick={() => {
                              const ui = useUiStore.getState();
                              ui.setRightPanelTab('diff');
                              ui.setRightPanelVisible(true);
                              setAgentChatVisible(false); // 收起聊天面板，让出审核视野
                            }}
                          >
                            去审核
                          </Button>
                          <Button
                            size="small"
                            icon={<CheckCircleOutlined />}
                            onClick={() => useReviewStore.getState().accept('chat')}
                          >
                            直接批准
                          </Button>
                          <Button
                            size="small"
                            onClick={() => useReviewStore.getState().defer()}
                          >
                            稍后
                          </Button>
                        </Space>
                      </div>
                    }
                  />
                ) : (
                  <Alert
                    type="warning"
                    message={
                      review.reviewType === 'bash_command'
                        ? '🛡️ 敏感命令请求审核'
                        : `🔔 审核请求 — ${review.reviewType}`
                    }
                    description={
                      <div>
                        <p><strong>{review.title}</strong></p>
                        <pre style={{ maxHeight: 150, overflow: 'auto', fontSize: 12 }}>
                          {review.content.slice(0, 1000)}
                        </pre>
                        <p style={{ marginTop: 8 }}>❓ {review.question}</p>
                        <Space style={{ marginTop: 8 }}>
                          <Button
                            type="primary"
                            size="small"
                            icon={<CheckCircleOutlined />}
                            onClick={() => useReviewStore.getState().accept('chat')}
                          >
                            批准
                          </Button>
                          <Button
                            size="small"
                            icon={<CloseCircleOutlined />}
                            onClick={() => useReviewStore.getState().reject('chat')}
                          >
                            拒绝
                          </Button>
                          <Button
                            size="small"
                            onClick={() => useReviewStore.getState().defer()}
                          >
                            稍后
                          </Button>
                        </Space>
                      </div>
                    }
                  />
                )}
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* 待审核小条（「稍后」折叠态）：常驻输入框上方，随时可回到审核 */}
          {review.status === 'pending' && review.deferred && (
            <div className="agent-review-minibar">
              <span className="agent-review-minibar-text">
                ⏳ 待审核：{review.title}
              </span>
              <Button
                size="small"
                type="link"
                onClick={() => {
                  const ui = useUiStore.getState();
                  ui.setRightPanelTab('diff');
                  ui.setRightPanelVisible(true);
                  setAgentChatVisible(false); // 收起聊天面板，让出审核视野
                }}
              >
                去审核
              </Button>
              <Button
                size="small"
                type="link"
                onClick={() => useReviewStore.getState().undefer()}
              >
                展开
              </Button>
            </div>
          )}

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

          {/* 会话状态栏（左下角） */}
          <div
            className="agent-chat-statusbar"
            style={{
              padding: '4px 12px',
              borderTop: '1px solid #f0f0f0',
              display: 'flex',
              alignItems: 'center',
              flexShrink: 0,
              background: '#fafafa',
            }}
          >
            <Tooltip title={`会话 ID: ${currentSessionId}`}>
              <Tag
                style={{
                  margin: 0,
                  fontSize: 12,
                  maxWidth: '100%',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {sessionTitle ? `${sessionTitle} · ${sessionTime}` : sessionTime}
              </Tag>
            </Tooltip>
          </div>
        </div>
      )}
    </>
  );
};

export default AgentChat;

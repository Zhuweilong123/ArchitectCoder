import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import {
  sendReviewResponse, type AgentEvent, type AgentProgressEvent, type AgentTodoItem,
} from '../../services/agentChat';
import { processDesignUpdated, restoreOriginalsToCanvas } from '../../services/designElementHandler';
import { useDiagramStore } from '../../stores/diagramStore';
import { useReviewStore } from '../../stores/reviewStore';
import { useUiStore } from '../../stores/uiStore';
import {
  normalizeReviewDiagrams, type ChatMessage,
} from './agentChatUtils';

type StateSetter<T> = Dispatch<SetStateAction<T>>;

export interface AgentChatEventHandlerOptions {
  setMessages: StateSetter<ChatMessage[]>;
  setBusy: StateSetter<boolean>;
  setCurrentSteps: StateSetter<AgentProgressEvent[]>;
  setCurrentTodos: StateSetter<AgentTodoItem[]>;
  setTodoPlanningMode: StateSetter<boolean>;
  setStrategyAdvised: StateSetter<boolean>;
  setTodoExpanded: StateSetter<boolean>;
  liveStepsRef: MutableRefObject<AgentProgressEvent[]>;
  liveTodosRef: MutableRefObject<AgentTodoItem[]>;
  todoSeenInTaskRef: MutableRefObject<boolean>;
  settleTodos: (terminalStatus: 'completed' | 'pending') => AgentTodoItem[];
  handleDesignElement: (event: { type: string; data: string }) => void;
}

const appendSystemMessage = (
  setMessages: StateSetter<ChatMessage[]>,
  message: Omit<ChatMessage, 'role'> & { role?: ChatMessage['role'] },
) => {
  setMessages((prev) => [...prev, { ...message, role: message.role || 'system' }]);
};

export function createAgentChatEventHandler({
  setMessages,
  setBusy,
  setCurrentSteps,
  setCurrentTodos,
  setTodoPlanningMode,
  setStrategyAdvised,
  setTodoExpanded,
  liveStepsRef,
  liveTodosRef,
  todoSeenInTaskRef,
  settleTodos,
  handleDesignElement,
}: AgentChatEventHandlerOptions): (event: AgentEvent) => void {
  return (event: AgentEvent) => {
    switch (event.event) {
      case 'progress_snapshot': {
        liveStepsRef.current = [...event.steps].sort((first, second) => first.step - second.step);
        setCurrentSteps([...liveStepsRef.current]);
        if (event.terminal_result_id) {
          setMessages(previous => previous.map(message => message.id === event.terminal_result_id
            ? { ...message, steps: [...liveStepsRef.current] } : message));
        }
        const latest = liveStepsRef.current[liveStepsRef.current.length - 1];
        if (latest && Array.isArray(latest.todos)) {
          liveTodosRef.current = latest.todos;
          setCurrentTodos(latest.todos);
          setTodoPlanningMode(Boolean(latest.planning_mode));
          setStrategyAdvised(Boolean(latest.strategy_advised));
        }
        break;
      }
      case 'run_started':
        setBusy(true);
        break;
      case 'session_sync':
        setBusy(event.running);
        if (event.pending_review_ids) {
          const review = useReviewStore.getState();
          if (review.status === 'pending' && review.reviewId !== null
            && !event.pending_review_ids.includes(review.reviewId)) review.clear();
        }
        if (event.replay_truncated) {
          appendSystemMessage(setMessages, {
            id: `replay_gap_${Date.now()}`,
            content: '部分历史事件已过期，可在 trace 中查看完整记录。当前任务状态已同步。',
            timestamp: Date.now(),
          });
        }
        break;
      case 'chat_chunk': {
        setMessages((prev) => {
          const lastIdx = prev.length - 1;
          const lastMsg = prev[lastIdx];
          if (lastMsg && lastMsg.role === 'agent' && lastMsg.id.startsWith('stream_')) {
            const next = [...prev];
            next[lastIdx] = { ...lastMsg, content: lastMsg.content + event.content };
            return next;
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

      case 'progress': {
        liveStepsRef.current = [
          ...liveStepsRef.current.filter((step) => step.step !== event.step),
          event,
        ].sort((a, b) => a.step - b.step);
        setCurrentSteps([...liveStepsRef.current]);
        if (Array.isArray(event.todos) && event.todos.length > 0) {
          liveTodosRef.current = event.todos;
          setCurrentTodos(event.todos);
          setTodoPlanningMode(Boolean(event.planning_mode));
          setStrategyAdvised(Boolean(event.strategy_advised));
          if (!todoSeenInTaskRef.current) {
            todoSeenInTaskRef.current = true;
            setTodoExpanded(true);
          }
        }
        break;
      }

      case 'request_review': {
        const isBash = event.review_type === 'bash_command';
        useReviewStore.getState().showReview({
          reviewId: event.review_id,
          reviewType: event.review_type,
          title: event.title,
          content: event.content,
          question: event.question,
        });
        appendSystemMessage(setMessages, {
          id: `review_${Date.now()}`,
          content: isBash
            ? `🛡️ Agent 请求执行敏感命令:\n\n${event.content}\n\n❓ ${event.question}`
            : `🔔 Agent 请求审核 [${event.review_type}]: ${event.title}\n\n${event.content}\n\n❓ ${event.question}`,
          timestamp: Date.now(),
          review: event,
        });
        break;
      }

      case 'contract_check': {
        if (event.status === 'block') {
          appendSystemMessage(setMessages, {
            id: `contract_check_${Date.now()}`,
            content: '设计契约校验阻止提交',
            timestamp: Date.now(),
          });
          break;
        }
        const statusText: Record<string, string> = {
          pass: '通过',
          warn: '发现警告',
          block: '阻止提交',
          inconclusive: '无法确定',
          not_applicable: '不适用',
        };
        const lines = [
          `设计契约校验：${statusText[event.status] || event.status}`,
          event.message,
        ];
        if (event.violations.length > 0) {
          lines.push(...event.violations.slice(0, 8).map((item) => {
            const location = item.path ? ` [${item.path}]` : '';
            return `- ${item.message}${location}`;
          }));
          if (event.violations.length > 8) {
            lines.push(`- 其余 ${event.violations.length - 8} 项请查看后端校验详情`);
          }
        }
        appendSystemMessage(setMessages, {
          id: `contract_check_${Date.now()}`,
          content: lines.join('\n'),
          timestamp: Date.now(),
        });
        break;
      }

      case 'uml_review': {
        const diagrams = normalizeReviewDiagrams(event.diagrams);
        if (diagrams.length === 0) {
          sendReviewResponse(event.review_id, '审核数据为空，请重新提交有效的 UML 差异', 'reject');
          appendSystemMessage(setMessages, {
            id: `review_invalid_${Date.now()}`,
            content: '审核请求缺少 UML 图数据，已拒绝该请求。请让 Agent 重新提交。',
            timestamp: Date.now(),
          });
          break;
        }
        const changedDiagrams = event.changed_diagrams === undefined
          ? undefined
          : normalizeReviewDiagrams(event.changed_diagrams);
        const snapshot: Record<string, any> = {};
        for (const spec of normalizeReviewDiagrams(event.original_diagrams)) {
          snapshot[`${spec.type}:${spec.name || ''}`] = spec.data;
        }
        processDesignUpdated(
          diagrams, [], useUiStore.getState(), useDiagramStore.getState(), snapshot,
          changedDiagrams,
        );
        useReviewStore.getState().showReview({
          reviewId: event.review_id,
          reviewType: 'uml_diff',
          title: event.title,
          auto: event.auto,
          question: '是否接受此变更？',
        });
        appendSystemMessage(setMessages, {
          id: `review_${Date.now()}`,
          content: event.auto
            ? `🛡️ ${event.title}\n\nAgent 修改了设计文件但未主动提交审核，框架已自动补推。请在右侧「差异对比」面板查看变更，并确认是否接受。`
            : `🔔 Agent 请求 UML 设计审核: ${event.title}\n\n请在右侧「差异对比」面板查看变更，并确认是否接受。`,
          timestamp: Date.now(),
        });
        break;
      }

      case 'project_committed': {
        const store = useDiagramStore.getState();
        const currentPath = (store.currentFilepath || '').replace(/\\/g, '/').toLowerCase();
        const committedPath = event.filepath.replace(/\\/g, '/').toLowerCase();
        if (currentPath && currentPath === committedPath) {
          const review = useReviewStore.getState();
          if (review.status === 'pending') break;
          store.markSaved(event.revision);
          if (review.status === 'accepted') review.clear();
        }
        break;
      }

      case 'review_timeout': {
        useReviewStore.getState().expire(
          `审核超时（${Math.round(event.timeout)}s 未响应），Agent 已继续执行`,
        );
        appendSystemMessage(setMessages, {
          id: `review_timeout_${Date.now()}`,
          content: `⏰ 审核「${event.title}」超时（${Math.round(event.timeout)}s 未响应），Agent 已继续执行。如需检查变更，请查看右侧「差异对比」面板。`,
          timestamp: Date.now(),
        });
        break;
      }

      case 'review_expired': {
        const canceled = event.reason === 'user_canceled_review';
        if (canceled && useReviewStore.getState().reviewId === event.review_id) {
          restoreOriginalsToCanvas(useUiStore.getState().originalDiagrams);
        }
        useDiagramStore.getState().endBatch();
        useReviewStore.getState().expire(canceled ? '已取消审核，候选未应用' : '审核请求已失效');
        setBusy(false);
        settleTodos('pending');
        appendSystemMessage(setMessages, {
          id: `review_expired_${Date.now()}`,
          content: canceled ? '已取消审核，候选未应用。' : '⚠️ 审核请求已失效，请重新发起请求。',
          timestamp: Date.now(),
        });
        break;
      }

      case 'done': {
        const resultId = event.event_epoch && event.event_seq
          ? `agent_result_${event.event_epoch}_${event.event_seq}` : `agent_${Date.now()}`;
        useDiagramStore.getState().endBatch();
        setBusy(false);
        const review = useReviewStore.getState();
        if (review.status === 'rejected') review.clear();
        const steps = liveStepsRef.current;
        liveStepsRef.current = [];
        setCurrentSteps([]);
        const todoStatus = !event.checkpoint || event.checkpoint.status === 'completed'
          ? 'completed'
          : 'pending';
        const finalTodos = settleTodos(todoStatus);
        const finalSteps = steps.map((step) => (
          Array.isArray(step.todos) && step.todos.length
            ? { ...step, todos: finalTodos }
            : step
        ));
        const recoveryAction = event.checkpoint?.stop_reason === 'contract_check_failed'
          && event.checkpoint.candidate_artifact
          ? { label: '修复设计契约并继续', message: '修复设计契约并继续' }
          : undefined;
        setMessages((prev) => {
          if (prev.some(message => message.id === resultId)) return prev;
          const hasStream = prev.some((message) => message.id.startsWith('stream_'));
          if (hasStream) {
            return prev.map((message) =>
              message.id.startsWith('stream_')
                ? {
                    ...message,
                    id: resultId,
                    content: event.result || message.content,
                    action: recoveryAction,
                    steps: finalSteps.length ? finalSteps : undefined,
                  }
                : message,
            );
          }
          return [
            ...prev,
            {
              id: resultId,
              role: 'agent' as const,
              content: event.result || '(空回复)',
              action: recoveryAction,
              timestamp: Date.now(),
              steps: finalSteps.length ? finalSteps : undefined,
            },
          ];
        });
        break;
      }

      case 'design_element':
        handleDesignElement(event);
        break;

      case 'stopped': {
        useDiagramStore.getState().endBatch();
        setBusy(false);
        settleTodos('pending');
        liveStepsRef.current = [];
        setCurrentSteps([]);
        useReviewStore.getState().expire('任务已停止，审核随之失效');
        setMessages((prev) => prev.map((message) =>
          message.id.startsWith('stream_')
            ? { ...message, id: message.id.replace('stream_', 'agent_') }
            : message,
        ));
        appendSystemMessage(setMessages, {
          id: `system_${Date.now()}`,
          content: `⏹️ ${event.reason}`,
          timestamp: Date.now(),
        });
        break;
      }

      case 'error': {
        if (event.running) {
          appendSystemMessage(setMessages, {
            id: `command_error_${Date.now()}`,
            content: event.message,
            timestamp: Date.now(),
          });
          break;
        }
        useDiagramStore.getState().endBatch();
        setBusy(false);
        settleTodos('pending');
        liveStepsRef.current = [];
        setCurrentSteps([]);
        useReviewStore.getState().expire('任务出错，审核随之失效');
        setMessages((prev) => prev.map((message) =>
          message.id.startsWith('stream_')
            ? { ...message, id: message.id.replace('stream_', 'agent_') }
            : message,
        ));
        appendSystemMessage(setMessages, {
          id: `error_${Date.now()}`,
          content: `❌ ${event.message}`,
          timestamp: Date.now(),
        });
        break;
      }
    }
  };
}

import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import {
  type AgentEvent, type AgentProgressEvent, type AgentTodoItem,
} from '../../services/agentChat';
import { processDesignUpdated } from '../../services/designElementHandler';
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
        useDiagramStore.getState().endBatch();
        useReviewStore.getState().expire('连接中断期间后端已取消该任务');
        setBusy(false);
        settleTodos('pending');
        appendSystemMessage(setMessages, {
          id: `review_expired_${Date.now()}`,
          content: '⚠️ 审核已失效（连接中断期间后端已取消该任务）。请重新发起请求。',
          timestamp: Date.now(),
        });
        break;
      }

      case 'done': {
        useDiagramStore.getState().endBatch();
        setBusy(false);
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
        setMessages((prev) => {
          const hasStream = prev.some((message) => message.id.startsWith('stream_'));
          if (hasStream) {
            return prev.map((message) =>
              message.id.startsWith('stream_')
                ? {
                    ...message,
                    id: message.id.replace('stream_', 'agent_'),
                    content: event.result || message.content,
                    steps: finalSteps.length ? finalSteps : undefined,
                  }
                : message,
            );
          }
          return [
            ...prev,
            {
              id: `agent_${Date.now()}`,
              role: 'agent' as const,
              content: event.result || '(空回复)',
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

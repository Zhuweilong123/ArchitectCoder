/**
 * 审核状态共享 store — AgentChat 审核卡与 DiffViewer 的单一事实源。
 *
 * 设计：一处决策，两处呈现。同一次审核在聊天卡和 diff 面板都可见，
 * 任一界面操作后另一界面通过订阅本 store 即时联动：
 *
 *   pending ──accept()──► accepted        （批准：落盘评审 + 回复 agent）
 *           ──reject()──► rejected        （拒绝：回滚画布 + 意见喂回 agent 修订）
 *           ──defer()───► pending(折叠小条)（稍后：不消费审核，保留入口）
 *           ──expire()──► expired         （超时/断线/任务中断 → 终态置灰）
 *
 * agent 修订后重新提交审核 → showReview() 再次进入 pending，两处同时刷新。
 */

import { create } from 'zustand';
import { message } from 'antd';
import { sendReviewResponse, setPendingUmlReviewId } from '../services/agentChat';
import { saveReview } from '../services/api';
import { restoreOriginalsToCanvas } from '../services/designElementHandler';
import { useUiStore } from './uiStore';

export type ReviewStatus = 'idle' | 'pending' | 'accepted' | 'rejected' | 'expired';
export type ReviewFrom = 'chat' | 'diff';

interface ShowReviewInput {
  reviewId: number;
  reviewType: string;            // 'uml_diff' | 'bash_command' | ...
  title: string;
  content?: string;
  question?: string;
}

interface ReviewState {
  reviewId: number | null;
  reviewType: string;
  title: string;
  content: string;
  question: string;
  status: ReviewStatus;
  actedFrom: ReviewFrom | null;  // 哪边完成的决策（用于界面展示）
  deferred: boolean;             // 「稍后」：审核仍 pending，聊天卡折叠为小条
  expiredReason: string;         // expired 时的说明文案

  showReview: (r: ShowReviewInput) => void;
  accept: (from: ReviewFrom, comment?: string) => void;
  reject: (from: ReviewFrom, feedback?: string) => void;
  defer: () => void;
  undefer: () => void;
  expire: (reason: string) => void;
  clear: () => void;
}

const INITIAL = {
  reviewId: null as number | null,
  reviewType: '',
  title: '',
  content: '',
  question: '',
  status: 'idle' as ReviewStatus,
  actedFrom: null as ReviewFrom | null,
  deferred: false,
  expiredReason: '',
};

export const useReviewStore = create<ReviewState>((set, get) => ({
  ...INITIAL,

  showReview: ({ reviewId, reviewType, title, content = '', question = '' }) => {
    set({
      reviewId, reviewType, title, content, question,
      status: 'pending', actedFrom: null, deferred: false, expiredReason: '',
    });
    // 登记送达跟踪标记（agentChat 内部用于断线清理/补发对账）
    if (reviewType === 'uml_diff') {
      setPendingUmlReviewId(reviewId);
    }
  },

  accept: (from, comment) => {
    const s = get();
    if (s.status !== 'pending' || s.reviewId === null) return;
    set({ status: 'accepted', actedFrom: from, deferred: false });

    const text = comment || '批准，继续';
    const result = sendReviewResponse(s.reviewId, text, 'accept');

    if (s.reviewType === 'uml_diff') {
      const ui = useUiStore.getState();
      saveReview({
        action: 'accept',
        comment: text,
        requirements: ui.optimizeInstructions || '',
        original_name: ui.originalDiagram?.name || '',
        optimized_name: ui.optimizedDiagram?.name || s.title,
        timestamp: new Date().toISOString(),
      }).catch((e) => console.warn('[ReviewStore] saveReview failed:', e));
    }
    if (result === 'queued') {
      message.info('连接已断开，正在重连并补发审核结果…');
    } else if (result === 'failed') {
      message.warning('审核回复发送失败，请检查后端连接后重试。');
    }
  },

  reject: (from, feedback) => {
    const s = get();
    if (s.status !== 'pending' || s.reviewId === null) return;
    set({ status: 'rejected', actedFrom: from, deferred: false });

    // 拒绝 = 回滚画布到审核前 + 意见喂回 agent 修订（修订后会推新审核）
    if (s.reviewType === 'uml_diff') {
      restoreOriginalsToCanvas(useUiStore.getState().originalDiagrams);
    }
    const text = feedback || '拒绝，请修改';
    const result = sendReviewResponse(s.reviewId, text, 'reject');

    if (s.reviewType === 'uml_diff') {
      const ui = useUiStore.getState();
      saveReview({
        action: 'reject',
        comment: text,
        requirements: ui.optimizeInstructions || '',
        original_name: ui.originalDiagram?.name || '',
        optimized_name: ui.optimizedDiagram?.name || s.title,
        timestamp: new Date().toISOString(),
      }).catch((e) => console.warn('[ReviewStore] saveReview failed:', e));
    }
    if (result === 'queued') {
      message.info('连接已断开，正在重连并补发反馈给 Agent…');
    } else if (result === 'failed') {
      message.warning('反馈发送失败；画布已回滚，可重新发起优化。');
    }
  },

  defer: () => {
    if (get().status === 'pending') set({ deferred: true });
  },

  undefer: () => set({ deferred: false }),

  expire: (reason) => {
    const s = get();
    if (s.status !== 'pending') return;
    set({ status: 'expired', expiredReason: reason, deferred: false });
    if (s.reviewType === 'uml_diff') {
      setPendingUmlReviewId(null);
    }
  },

  clear: () => {
    if (get().reviewType === 'uml_diff') {
      setPendingUmlReviewId(null);
    }
    set({ ...INITIAL });
  },
}));

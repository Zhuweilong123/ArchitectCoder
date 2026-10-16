/** Shared review decisions and delivery state for chat and the diff panel. */
import { create } from 'zustand';
import { message } from 'antd';
import {
  sendReviewResponse, setPendingUmlReviewId, onAgentMessage, discardReviewResponse,
} from '../services/agentChat';
import { saveProject, saveReview } from '../services/api';
import { restoreOriginalsToCanvas } from '../services/designElementHandler';
import { useUiStore } from './uiStore';
import { useDiagramStore } from './diagramStore';

// accepted/rejected mean the reply was sent, not that the backend committed it.
export type ReviewStatus = 'idle' | 'pending' | 'submitting' | 'accepted' | 'rejected' | 'expired';
export type ReviewFrom = 'chat' | 'diff';

interface ShowReviewInput {
  reviewId: number;
  reviewType: string;
  title: string;
  content?: string;
  question?: string;
  auto?: boolean;
}

interface ReviewState {
  reviewId: number | null;
  reviewType: string;
  autoReview: boolean;
  title: string;
  content: string;
  question: string;
  status: ReviewStatus;
  delivery: 'sending' | 'queued' | null;
  actedFrom: ReviewFrom | null;
  deferred: boolean;
  expiredReason: string;
  showReview: (r: ShowReviewInput) => void;
  accept: (from: ReviewFrom, comment?: string) => Promise<boolean>;
  reject: (from: ReviewFrom, feedback?: string) => Promise<boolean>;
  defer: () => void;
  undefer: () => void;
  expire: (reason: string, reviewId?: number) => void;
  clear: () => void;
}

const INITIAL = {
  reviewId: null as number | null,
  reviewType: '', autoReview: false, title: '', content: '', question: '',
  status: 'idle' as ReviewStatus,
  delivery: null as ReviewState['delivery'],
  actedFrom: null as ReviewFrom | null,
  deferred: false, expiredReason: '',
};

export const useReviewStore = create<ReviewState>((set, get) => {
  interface Submission {
    review: ReviewState & { reviewId: number };
    decision: 'accept' | 'reject';
    from: ReviewFrom;
    log: Parameters<typeof saveReview>[0];
    originals: ReturnType<typeof useUiStore.getState>['originalDiagrams'];
    completion?: Promise<void>;
  }
  let active: Submission | null = null;

  const cancelSubmission = () => {
    if (active) discardReviewResponse(active.review.reviewId);
    active = null;
  };
  const isCurrent = (submission: Submission) => active === submission
    && get().reviewId === submission.review.reviewId;

  const complete = (submission: Submission): Promise<void> => {
    if (submission.completion) return submission.completion;
    if (!isCurrent(submission)) return Promise.resolve();
    submission.completion = (async () => {
      const { review, decision, originals } = submission;
      set({ status: decision === 'accept' ? 'accepted' : 'rejected',
        delivery: null, actedFrom: submission.from, deferred: false });
      if (review.reviewType !== 'uml_diff') return;
      if (decision === 'reject' && !review.autoReview) restoreOriginalsToCanvas(originals);
      useUiStore.getState().setDesignComparisonVisible(false);
      const reply = decision === 'accept' ? '批准回复已发送' : '拒绝反馈已发送';
      try {
        const result = await saveReview(submission.log);
        if (!result.success) throw new Error('Review log was not saved');
        if (isCurrent(submission)) message.success(reply + '，评审已保存到 dev_review.txt');
      } catch (error) {
        console.warn('[ReviewStore] saveReview failed:', error);
        if (isCurrent(submission)) message.warning(reply + '，但评审日志保存失败；请勿重复发送审核回复。');
      }
    })();
    return submission.completion;
  };

  onAgentMessage((event) => {
    const submission = active;
    if (!submission || event.review_id !== submission.review.reviewId) return;
    if (event.event === 'review_resolved') void complete(submission);
    if (event.event === 'review_delivery_failed' && get().status === 'submitting') {
      active = null;
      set({ status: 'pending', delivery: null, actedFrom: null });
      message.warning('审核回复未能发送，请检查连接后重试。');
    }
  });

  const submit = async (decision: 'accept' | 'reject', from: ReviewFrom, feedback?: string) => {
    const review = get();
    if (review.status !== 'pending' || review.reviewId === null) return false;
    const ui = useUiStore.getState();
    const text = feedback || (decision === 'accept' ? '批准，继续' : '拒绝，请修改');
    const submission: Submission = {
      review: { ...review, reviewId: review.reviewId }, decision, from,
      originals: ui.originalDiagrams,
      log: { action: decision, comment: text, requirements: ui.optimizeInstructions || '',
        original_name: ui.originalDiagram?.name || '',
        optimized_name: ui.optimizedDiagram?.name || review.title, timestamp: new Date().toISOString() },
    };
    active = submission;
    set({ status: 'submitting', delivery: 'sending', deferred: false });

    // Fallback reviews restore the persisted project before notifying the backend.
    // Live tool reviews are restored by the backend after receiving the reply.
    if (decision === 'reject' && review.reviewType === 'uml_diff' && review.autoReview) {
      const restoredProject = restoreOriginalsToCanvas(submission.originals);
      const filepath = useDiagramStore.getState().currentFilepath;
      if (restoredProject && filepath) {
        try {
          const result = await saveProject(useDiagramStore.getState().getProjectSnapshot(), filepath, false);
          if (!isCurrent(submission)) return false;
          useDiagramStore.getState().markSaved(result.revision);
        } catch (error) {
          console.error('[ReviewStore] failed to persist rejected UML changes:', error);
          if (isCurrent(submission)) {
            active = null;
            set({ status: 'pending', delivery: null });
            message.error('拒绝优化后恢复项目失败，未通知 Agent 继续执行');
          }
          return false;
        }
      }
    }
    if (!isCurrent(submission)) return false;
    const result = sendReviewResponse(review.reviewId, text, decision);
    if (result === 'failed') {
      active = null;
      set({ status: 'pending', delivery: null, actedFrom: null });
      message.warning('审核回复发送失败，请检查后端连接后重试。');
      return false;
    }
    if (result === 'queued') {
      set({ delivery: 'queued' });
      message.info('审核回复等待重连补发，尚未发送。');
      return true;
    }
    await complete(submission);
    return true;
  };

  return {
    ...INITIAL,
    showReview: ({ reviewId, reviewType, title, content = '', question = '', auto = false }) => {
      // Reconnect may replay the same request while its reply is queued or sent.
      const current = get();
      if (current.reviewId === reviewId && ['submitting', 'accepted', 'rejected'].includes(current.status)) return;
      cancelSubmission();
      set({ reviewId, reviewType, autoReview: auto, title, content, question,
        status: 'pending', delivery: null, actedFrom: null, deferred: false, expiredReason: '' });
      setPendingUmlReviewId(reviewType === 'uml_diff' ? reviewId : null);
    },
    accept: (from, comment) => submit('accept', from, comment),
    reject: (from, feedback) => submit('reject', from, feedback),
    defer: () => { if (get().status === 'pending') set({ deferred: true }); },
    undefer: () => set({ deferred: false }),
    expire: (reason, reviewId) => {
      const current = get();
      if (reviewId !== undefined && current.reviewId !== reviewId) return;
      if (current.status === 'idle' || current.status === 'expired') return;
      if (reviewId === undefined && current.status !== 'pending' && current.status !== 'submitting') return;
      cancelSubmission();
      set({ status: 'expired', delivery: null, expiredReason: reason, deferred: false });
      if (current.reviewType === 'uml_diff') {
        useUiStore.getState().setDesignComparisonVisible(false);
        setPendingUmlReviewId(null);
      }
    },
    clear: () => {
      cancelSubmission();
      if (get().reviewType === 'uml_diff') setPendingUmlReviewId(null);
      set({ ...INITIAL });
    },
  };
});

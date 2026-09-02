/**
 * Diff Viewer – diff display, canvas toggle, review, continue optimization.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Empty, Button, message, Tag, Input, Modal } from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined,
  SwapOutlined, FileTextOutlined, ReloadOutlined,
  ApartmentOutlined, ClockCircleOutlined, BlockOutlined,
} from '@ant-design/icons';
import Editor from '@monaco-editor/react';
import * as Diff from 'diff';
import { useUiStore, DiffDiagramType } from '../../stores/uiStore';
import { useDiagramStore } from '../../stores/diagramStore';
import { saveReview } from '../../services/api';
import { sendAgentMessage } from '../../services/agentChat';
import { restoreOriginalsToCanvas } from '../../services/designElementHandler';
import { useReviewStore } from '../../stores/reviewStore';
import './DiffViewer.css';

const { TextArea } = Input;

const DiffViewer: React.FC = () => {
  const { setDiagram, diagram, setActiveDiagram, project, currentFilepath, triggerRecenter } = useDiagramStore();
  const {
    originalCode, optimizedCode, diffContent,
    originalDiagram, optimizedDiagram,
    originalDiagrams, optimizedDiagrams, diffContents,
    activeDiffDiagramType, optimizationConsistencyReport,
    showingOptimized, toggleShowingVersion,
    setRightPanelTab,
    setGlobalOptimizationResult, setActiveDiffDiagramType,
    optimizeInstructions,
  } = useUiStore();

  // Check if we're in multi-diagram mode (pipeline global optimize)
  const hasMultiDiagrams = Object.keys(optimizedDiagrams).length > 0;
  const availableTypes: DiffDiagramType[] = hasMultiDiagrams
    ? (Object.keys(optimizedDiagrams) as DiffDiagramType[])
    : (originalDiagram ? [(originalDiagram.diagram_type || 'class') as DiffDiagramType] : ['class']);

  // Format "type:name" compound key for display
  const formatTypeLabel = (key: DiffDiagramType): string => {
    const { dtype, dname } = parseDiagramKey(key);
    const info = TYPE_LABELS[dtype];
    const baseLabel = info?.label || dtype;
    return dname ? `${baseLabel} · ${dname}` : baseLabel;
  };

  // 空工程/全新设计：原始版和优化版内容相同，不需要切换按钮
  const activeOrig = hasMultiDiagrams ? originalDiagrams[activeDiffDiagramType] : originalDiagram;
  const activeOpt = hasMultiDiagrams ? optimizedDiagrams[activeDiffDiagramType] : optimizedDiagram;
  const isNewDesign = activeOrig && activeOpt
    && JSON.stringify(activeOrig) === JSON.stringify(activeOpt);

  const TYPE_LABELS: Record<string, { label: string; icon: React.ReactNode }> = {
    class: { label: '类图', icon: <ApartmentOutlined /> },
    sequence: { label: '时序图', icon: <ClockCircleOutlined /> },
    component: { label: '组件图', icon: <BlockOutlined /> },
  };

  // Parse compound key "type:name" into {type, name}; falls back to type-only for legacy keys
  const parseDiagramKey = (key: string): { dtype: string; dname: string } => {
    const colonIdx = key.indexOf(':');
    if (colonIdx > 0) {
      return { dtype: key.substring(0, colonIdx), dname: key.substring(colonIdx + 1) };
    }
    return { dtype: key, dname: '' };
  };

  const [reviewComment, setReviewComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [resolved, setResolved] = useState(false);
  const [rejectModalVisible, setRejectModalVisible] = useState(false);
  const [rejectInstructions, setRejectInstructions] = useState('');
  const [reoptimizing, setReoptimizing] = useState(false);

  // ── 与 AgentChat 联动的共享审核状态（reviewStore 为单一事实源）──
  // reviewLinked：当前 diff 数据伴随一次 agent 审核（uml_review）
  // reviewPending：审核待决策 → 本面板是审核工作台，按钮可用
  // 终态（accepted/rejected/expired）→ 按钮锁定，header 显示结果
  const reviewId = useReviewStore((s) => s.reviewId);
  const reviewStatus = useReviewStore((s) => s.status);
  const reviewType = useReviewStore((s) => s.reviewType);
  const reviewActedFrom = useReviewStore((s) => s.actedFrom);
  const reviewLinked = reviewId !== null && reviewType === 'uml_diff';
  const reviewPending = reviewLinked && reviewStatus === 'pending';
  // 联动模式下完成态由 store 决定；独立流程（单图优化/Pipeline）用本地 resolved
  const resolvedEff = reviewLinked ? reviewStatus !== 'pending' : resolved;

  // When new optimization result arrives, reset to fresh state
  useEffect(() => {
    setResolved(false);
    setReviewComment('');
    setSaving(false);
    setReoptimizing(false);
    setRejectModalVisible(false);
    // 新对比数据到达且上一审核已终结（独立流程）→ 解除联动，避免按钮卡在旧状态
    const st = useReviewStore.getState();
    if (st.reviewId !== null && st.status !== 'pending') {
      st.clear();
    }
  }, [optimizedCode]);

  // Toggle canvas between original and optimized (supports multi-diagram)
  const handleToggleCanvas = () => {
    // Ensure the active diagram matches the diff tab (in case user switched via toolbar)
    const targetType = hasMultiDiagrams ? activeDiffDiagramType : (originalDiagram?.diagram_type || 'class');
    const { dtype, dname } = parseDiagramKey(targetType);
    const targetIdx = dname
      ? project.diagrams.findIndex(
          d => (d.diagram_type || 'class') === dtype && d.name === dname
        )
      : project.diagrams.findIndex(
          d => (d.diagram_type || 'class') === targetType
        );
    const currentActiveIdx = project.active_diagram_index;
    if (targetIdx >= 0 && targetIdx !== currentActiveIdx) {
      setActiveDiagram(targetIdx);
    }
    // Toggle between original and optimized using our stored copies
    // (NOT project's stored version, which may have been polluted by prior toggles)
    if (showingOptimized) {
      if (originalDiagram) setDiagram(originalDiagram);
    } else {
      if (optimizedDiagram) setDiagram(optimizedDiagram);
    }
    toggleShowingVersion();
  };

  // Handle diagram-type tab switch → also switch main canvas and restore original
  const handleTypeSwitch = (type: DiffDiagramType) => {
    setActiveDiffDiagramType(type);
    // Switch canvas to the correct diagram (type:name compound key)
    const { dtype, dname } = parseDiagramKey(type);
    const targetIdx = dname
      ? project.diagrams.findIndex(
          d => (d.diagram_type || 'class') === dtype && d.name === dname
        )
      : project.diagrams.findIndex(
          d => (d.diagram_type || 'class') === dtype
        );
    if (targetIdx >= 0) {
      setActiveDiagram(targetIdx);
      // Always restore the original version on tab switch,
      // because the project's stored version may have been polluted by a prior toggle
      const orig = originalDiagrams[type];
      if (orig) {
        setDiagram(orig);
      }
    }
  };

  // Accept: show confirmation dialog first
  const handleAcceptClick = () => {
    Modal.confirm({
      title: '确认接受优化',
      content: '接受后画布将更新为优化版本，评审记录将保存到 dev_review.txt。确定接受吗？',
      okText: '确定接受',
      cancelText: '取消',
      onOk: handleAcceptConfirm,
    });
  };

  const handleAcceptConfirm = async () => {
    // ── 联动模式：委托共享 store（回复 agent + 落盘评审由 store 统一处理）──
    if (reviewPending) {
      useReviewStore.getState().accept('diff', reviewComment);
      message.success('已接受优化结果，评审已保存到 dev_review.txt');
      setRightPanelTab('properties');
      // 触发画布居中，确保用户可以看到更新后的图
      triggerRecenter();
      return;
    }
    if (!optimizedDiagram && !hasMultiDiagrams) return;
    setSaving(true);
    try {
      if (hasMultiDiagrams) {
        // Apply all optimized diagrams — auto-create tabs for new ones
        for (const type of Object.keys(optimizedDiagrams) as DiffDiagramType[]) {
          const opt = optimizedDiagrams[type];
          if (!opt) continue;
          const { dtype, dname } = parseDiagramKey(type);
          let idx = dname
            ? project.diagrams.findIndex(
                d => (d.diagram_type || 'class') === dtype && d.name === dname
              )
            : project.diagrams.findIndex(
                d => (d.diagram_type || 'class') === type
              );
          if (idx >= 0) {
            const updatedDiagrams = [...project.diagrams];
            updatedDiagrams[idx] = { ...updatedDiagrams[idx], ...opt };
            useDiagramStore.setState({
              project: { ...project, diagrams: updatedDiagrams },
              diagram: updatedDiagrams[project.active_diagram_index],
              isModified: true,
            });
          } else {
            // Auto-create missing diagram
            const store = useDiagramStore.getState();
            store.addDiagram(dtype, opt.name || dname || dtype, opt.component_id || '');
            const newIdx = store.project.diagrams.length - 1;
            const updatedDiagrams = [...store.project.diagrams];
            updatedDiagrams[newIdx] = { ...updatedDiagrams[newIdx], ...opt };
            useDiagramStore.setState({
              project: { ...store.project, diagrams: updatedDiagrams },
              diagram: updatedDiagrams[store.project.active_diagram_index],
              isModified: true,
            });
          }
        }
      } else {
        setDiagram(optimizedDiagram!);
      }
      await saveReview({
        action: 'accept',
        comment: reviewComment,
        requirements: optimizeInstructions,
        original_name: originalDiagram?.name || '',
        optimized_name: optimizedDiagram?.name || '',
        timestamp: new Date().toISOString(),
      });
      message.success('已接受优化结果，评审已保存到 dev_review.txt');
      setResolved(true);
      setRightPanelTab('properties');
      // 触发画布居中，确保用户可以看到更新后的图
      triggerRecenter();
    } catch (e) {
      message.error('保存评审失败: ' + String(e));
    }
    setSaving(false);
  };

  // Reject: open dialog with new optimization input
  const handleRejectClick = () => {
    setRejectInstructions('');
    setRejectModalVisible(true);
  };

  const handleCancelReject = () => {
    setRejectModalVisible(false);
  };

  const handleContinueOptimize = async () => {
    setReoptimizing(true);
    const dt = originalDiagram?.diagram_type || 'class';

    // ── 联动模式：拒绝 + 意见喂回 Agent ──
    // 回滚画布 / 落盘评审 / 回复阻塞中的 agent 由共享 store 统一处理
    if (reviewPending) {
      const rejected = await useReviewStore.getState().reject('diff', rejectInstructions);
      if (!rejected) {
        setReoptimizing(false);
        return;
      }
      setRejectModalVisible(false);
      setReviewComment('');
      message.success({ content: '已拒绝并反馈给 AI 助手，Agent 将带着反馈继续修改', key: 'reoptimize' });
      setReoptimizing(false);
      return;
    }

    message.loading({ content: 'LLM 正在重新优化...', key: 'reoptimize' });
    try {
      if (hasMultiDiagrams) {
        // Re-run global optimization via Agent WebSocket
        // （sendAgentMessage 内部会用常驻事件回调自愈重连，无需先 connect；
        //  此处若 connectAgentChat(() => {}) 反而会用空回调覆盖掉常驻 handler）
        sendAgentMessage(
          `请对当前项目进行全局UML交叉验证和优化: ${rejectInstructions}`,
          { project_file: currentFilepath || '' },
        );
        // 优化结果通过 Agent WebSocket 的 design_updated 事件异步返回
        await saveReview({
          action: 'reject',
          comment: rejectInstructions || reviewComment || '(继续优化)',
          requirements: optimizeInstructions,
          original_name: originalDiagram?.name || '',
          optimized_name: optimizedDiagram?.name || '',
          timestamp: new Date().toISOString(),
        });
        setRejectModalVisible(false);
        setResolved(false);
        setReviewComment('');
        message.success({ content: '已发送重新优化请求到 AI 助手，请查看聊天面板', key: 'reoptimize' });
      }
    } catch (e) {
      message.error({ content: '重新优化失败: ' + String(e), key: 'reoptimize' });
    }
    setReoptimizing(false);
  };

  const handleCancelOptimize = async () => {
    // ── 联动模式：拒绝但不附意见（回滚/落盘/回复 agent 由 store 统一处理）──
    if (reviewPending) {
      const rejected = await useReviewStore.getState().reject('diff', reviewComment || '用户拒绝了此次变更');
      if (!rejected) return;
      message.info('已拒绝优化结果，评审已保存到 dev_review.txt');
      setRejectModalVisible(false);
      setRightPanelTab('properties');
      return;
    }
    // Just save review and close, no further optimization
    setSaving(true);
    try {
      await saveReview({
        action: 'reject',
        comment: reviewComment,
        requirements: optimizeInstructions,
        original_name: originalDiagram?.name || '',
        optimized_name: optimizedDiagram?.name || '',
        timestamp: new Date().toISOString(),
      });
      if (hasMultiDiagrams) {
        // 画布回滚到审核前版本（processDesignUpdated 已预写入优化版）
        restoreOriginalsToCanvas(originalDiagrams);
      } else if (showingOptimized && originalDiagram) {
        setDiagram(originalDiagram);
        toggleShowingVersion();
      }
      message.info('已拒绝优化结果，评审已保存到 dev_review.txt');
      setResolved(true);
      setRejectModalVisible(false);
      setRightPanelTab('properties');
    } catch (e) {
      message.error('保存评审失败: ' + String(e));
    }
    setSaving(false);
  };

  // Generate unified diff text
  const unifiedDiff = useMemo(() => {
    if (!originalCode || !optimizedCode) return '';
    const origKey = Object.keys(originalCode)[0];
    const optKey = Object.keys(optimizedCode)[0];
    if (!origKey || !optKey) return '';

    const orig = originalCode[origKey] || '';
    const opt = optimizedCode[optKey] || '';

    let origFormatted = orig;
    let optFormatted = opt;
    try {
      origFormatted = JSON.stringify(JSON.parse(orig), null, 2);
      optFormatted = JSON.stringify(JSON.parse(opt), null, 2);
    } catch {}

    const dt = hasMultiDiagrams ? activeDiffDiagramType : (originalDiagram?.diagram_type || 'class');
    const labelMap: Record<string, string> = { class: 'Class Diagram', sequence: 'Sequence Diagram', component: 'Component Diagram' };
    const diagramLabel = labelMap[dt] || 'UML Diagram';
    return Diff.createPatch(diagramLabel, origFormatted, optFormatted,
      'Original', 'Optimized');
  }, [originalCode, optimizedCode]);

  if (!originalCode || !optimizedCode) {
    return (
      <div className="diff-viewer">
        <Empty description="暂无对比数据" image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <p>使用"全局优化"功能生成设计优化对比</p>
        </Empty>
      </div>
    );
  }

  const buttonsDisabled = resolvedEff && !reoptimizing;

  return (
    <div className="diff-viewer">
      {/* Header with toggle */}
      <div className="diff-header">
        <h3>
          {hasMultiDiagrams
            ? '全局优化对比'
            : (originalDiagram?.diagram_type === 'sequence' ? '时序图优化对比' : 'UML 优化对比')}
        </h3>
        {/* 与 AgentChat 联动的审核状态徽标 */}
        {reviewPending && <Tag color="red">待审核</Tag>}
        {reviewLinked && reviewStatus === 'accepted' && (
          <Tag color="success">已批准{reviewActedFrom === 'chat' ? ' · 快捷批准' : ''}</Tag>
        )}
        {reviewLinked && reviewStatus === 'rejected' && <Tag color="warning">已拒绝 · Agent 修订中</Tag>}
        {reviewLinked && reviewStatus === 'expired' && <Tag>已失效</Tag>}
        {isNewDesign ? (
          <Tag color="purple" style={{ fontSize: 12 }}>从需求全新生成</Tag>
        ) : (
          <Button
            icon={<SwapOutlined />}
            size="small"
            type={showingOptimized ? 'primary' : 'default'}
            onClick={handleToggleCanvas}
          >
            {showingOptimized ? '画布: 优化版' : '画布: 原始版'}
          </Button>
        )}
      </div>

      {/* Diagram type tabs (shown when multi-diagram data is available) */}
      {hasMultiDiagrams && availableTypes.length > 1 && (
        <div className="diff-type-tabs" style={{ display: 'flex', gap: 4, marginBottom: 8, flexWrap: 'wrap' }}>
          {availableTypes.map(type => {
            const info = TYPE_LABELS[parseDiagramKey(type).dtype];
            const hasData = !!optimizedDiagrams[type];
            return (
              <Button
                key={type}
                size="small"
                type={activeDiffDiagramType === type ? 'primary' : 'default'}
                icon={info?.icon}
                disabled={!hasData}
                onClick={() => handleTypeSwitch(type)}
              >
                {formatTypeLabel(type)}
              </Button>
            );
          })}
        </div>
      )}

      {/* Consistency report (global optimization cross-validation findings) */}
      {optimizationConsistencyReport && optimizationConsistencyReport.length > 0 && (() => {
        const errors = optimizationConsistencyReport.filter((i: any) => i.severity === 'error' && !i.auto_fixed);
        const warnings = optimizationConsistencyReport.filter((i: any) => i.severity === 'warning' && !i.auto_fixed);
        const infos = optimizationConsistencyReport.filter((i: any) => i.severity === 'info' || i.auto_fixed);
        return (
          <div className="diff-summary" style={{ backgroundColor: '#fff7e6', borderLeft: '3px solid #faad14' }}>
            <Tag color="orange">一致性报告</Tag>
            {errors.map((item: any, i: number) => (
              <p key={`err_${i}`} style={{ fontSize: 12, margin: '2px 0', color: '#ff4d4f' }}>
                ❌ {item.msg}
              </p>
            ))}
            {warnings.map((item: any, i: number) => (
              <p key={`warn_${i}`} style={{ fontSize: 12, margin: '2px 0', color: '#d48806' }}>
                ⚠️ {item.msg}
              </p>
            ))}
            {infos.map((item: any, i: number) => (
              <p key={`info_${i}`} style={{ fontSize: 12, margin: '2px 0', color: '#52c41a' }}>
                <Tag color="green" style={{ fontSize: 10, lineHeight: '16px', marginRight: 4 }}>已自动修复</Tag>
                {item.msg}
              </p>
            ))}
            <p style={{ fontSize: 11, color: '#888', marginTop: 6 }}>
              {errors.length > 0 && `${errors.length} 个错误 `}
              {warnings.length > 0 && `${warnings.length} 个警告 `}
              {infos.length > 0 && `${infos.length} 个已自动修复`}
            </p>
          </div>
        );
      })()}

      {/* Toggle hint */}
      <div className="diff-toggle-hint">
        <Tag color={showingOptimized ? 'blue' : 'default'}>
          当前画布显示: {showingOptimized ? '优化后版本 (可编辑)' : '原始版本'}
        </Tag>
        <span style={{ fontSize: 11, color: '#888' }}>
          点击右侧按钮切换画布上的新旧版本，方便对比
        </span>
      </div>

      {/* Diff summary */}
      {diffContent && (
        <div className="diff-summary">
          <Tag color="blue">变更摘要</Tag>
          <p>{diffContent}</p>
        </div>
      )}

      {/* Diff editor */}
      <div className="diff-editor-wrapper">
        <Editor
          height="100%"
          language="diff"
          value={unifiedDiff}
          theme="vs-dark"
          options={{
            readOnly: true,
            minimap: { enabled: false },
            fontSize: 12,
            lineNumbers: 'on',
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            automaticLayout: true,
          }}
        />
      </div>

      {/* Review comments */}
      <div className="diff-review">
        <div className="diff-review-header">
          <FileTextOutlined />
          <span>评审意见</span>
        </div>
        <TextArea
          value={reviewComment}
          onChange={(e) => setReviewComment(e.target.value)}
          placeholder={'输入评审意见...\n例如：\n• 组合关系改得好\n• 需要补充User的validate方法\n• 建议保留原来的Order类名'}
          rows={3}
          disabled={resolvedEff}
        />
      </div>

      {/* Accept / Reject buttons */}
      <div className="diff-actions">
        <Button
          type="primary"
          icon={<CheckCircleOutlined />}
          onClick={handleAcceptClick}
          loading={saving}
          disabled={buttonsDisabled || saving || reoptimizing}
          block
        >
          {resolvedEff ? '已完成评审' : '接受优化（保存评审）'}
        </Button>
        <Button
          danger
          icon={<CloseCircleOutlined />}
          onClick={handleRejectClick}
          loading={false}
          disabled={buttonsDisabled || saving || reoptimizing}
          block
        >
          {resolvedEff ? '已完成评审' : '拒绝优化'}
        </Button>
      </div>
      <div style={{ fontSize: 10, color: '#999', textAlign: 'center', marginTop: 4 }}>
        评审记录将保存在 backend/dev_review.txt
        {resolvedEff && ' | 评审已完成，如需重新优化请点击"全局优化"按钮'}
      </div>

      {/* Reject → Continue Optimize Modal */}
      <Modal
        title="拒绝优化 — 输入新的优化需求"
        open={rejectModalVisible}
        onOk={handleContinueOptimize}
        onCancel={handleCancelOptimize}
        confirmLoading={reoptimizing}
        okText="继续优化"
        cancelText="放弃优化"
        width={550}
        footer={[
          <Button key="cancel" onClick={handleCancelReject}>
            取消
          </Button>,
          <Button
            key="discard"
            danger
            onClick={handleCancelOptimize}
            loading={saving}
          >
            放弃优化
          </Button>,
          <Button
            key="continue"
            type="primary"
            icon={<ReloadOutlined />}
            onClick={handleContinueOptimize}
            loading={reoptimizing}
          >
            继续优化
          </Button>,
        ]}
      >
        <p style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>
          已拒绝当前优化结果。你可以输入新的优化需求让 LLM 重新优化：
        </p>
        <TextArea
          value={rejectInstructions}
          onChange={(e) => setRejectInstructions(e.target.value)}
          placeholder={'输入新的优化需求，如：\n• 请重点优化类的职责划分\n• 改为使用策略模式\n• 补充缺失的getter/setter方法\n...'}
          rows={5}
          autoFocus
        />
        <div style={{ fontSize: 11, color: '#999', marginTop: 6 }}>
          点击"继续优化"将保存本次评审并提交新的优化请求；点击"放弃优化"直接取消。
        </div>
      </Modal>
    </div>
  );
};

export default DiffViewer;

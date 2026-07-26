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
import { saveReview, optimizeUml as apiOptimizeUml, optimizeProject as apiOptimizeProject } from '../../services/api';
import './DiffViewer.css';

const { TextArea } = Input;

const DiffViewer: React.FC = () => {
  const { setDiagram, diagram, setActiveDiagram, project } = useDiagramStore();
  const {
    originalCode, optimizedCode, diffContent,
    originalDiagram, optimizedDiagram,
    originalDiagrams, optimizedDiagrams, diffContents,
    activeDiffDiagramType, optimizationConsistencyReport,
    showingOptimized, toggleShowingVersion,
    setRightPanelTab, setOptimizationResult,
    setGlobalOptimizationResult, setActiveDiffDiagramType,
    optimizeInstructions,
  } = useUiStore();

  // Check if we're in multi-diagram mode (pipeline global optimize)
  const hasMultiDiagrams = Object.keys(optimizedDiagrams).length > 0;
  const availableTypes: DiffDiagramType[] = hasMultiDiagrams
    ? (Object.keys(optimizedDiagrams) as DiffDiagramType[])
    : (originalDiagram ? [(originalDiagram.diagram_type || 'class') as DiffDiagramType] : ['class']);

  const TYPE_LABELS: Record<DiffDiagramType, { label: string; icon: React.ReactNode }> = {
    class: { label: '类图', icon: <ApartmentOutlined /> },
    sequence: { label: '时序图', icon: <ClockCircleOutlined /> },
    component: { label: '组件图', icon: <BlockOutlined /> },
  };

  const [reviewComment, setReviewComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [resolved, setResolved] = useState(false);
  const [rejectModalVisible, setRejectModalVisible] = useState(false);
  const [rejectInstructions, setRejectInstructions] = useState('');
  const [reoptimizing, setReoptimizing] = useState(false);

  // When new optimization result arrives, reset to fresh state
  useEffect(() => {
    setResolved(false);
    setReviewComment('');
    setSaving(false);
    setReoptimizing(false);
    setRejectModalVisible(false);
  }, [optimizedCode]);

  // Toggle canvas between original and optimized (supports multi-diagram)
  const handleToggleCanvas = () => {
    // Ensure the active diagram matches the diff tab (in case user switched via toolbar)
    const targetType = hasMultiDiagrams ? activeDiffDiagramType : (originalDiagram?.diagram_type || 'class');
    const targetIdx = project.diagrams.findIndex(
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
    // Switch canvas to the correct diagram
    const targetIdx = project.diagrams.findIndex(
      d => (d.diagram_type || 'class') === type
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
    if (!optimizedDiagram && !hasMultiDiagrams) return;
    setSaving(true);
    try {
      if (hasMultiDiagrams) {
        // Apply all optimized diagrams — auto-create tabs for new ones
        for (const type of Object.keys(optimizedDiagrams) as DiffDiagramType[]) {
          const opt = optimizedDiagrams[type];
          if (!opt) continue;
          let idx = project.diagrams.findIndex(
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
            // Auto-create missing diagram type
            const store = useDiagramStore.getState();
            store.addDiagram(type, opt.name || type, opt.component_id || '');
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
    message.loading({ content: 'LLM 正在重新优化...', key: 'reoptimize' });
    try {
      if (hasMultiDiagrams) {
        // Re-run global optimization with all three diagrams
        const classOrig = originalDiagrams.class || originalDiagram;
        const result = await apiOptimizeProject({
          class_diagram: classOrig,
          sequence_diagram: originalDiagrams.sequence,
          component_diagram: originalDiagrams.component,
          instructions: rejectInstructions,
        }) as any;
        // Parse and push back to DiffViewer
        const optimized = result.optimized || {};
        const originals: Record<string, any> = {};
        const optimizeds: Record<string, any> = {};
        const diffs: Record<string, string> = {};
        for (const type of (Object.keys(optimized) as DiffDiagramType[])) {
          const orig = originalDiagrams[type] || (type === 'class' ? originalDiagram : null);
          const opt = optimized[type];
          if (orig && opt) {
            originals[type] = orig;
            // Merge LLM output with original metadata (diagram_type, name, etc.)
            optimizeds[type] = { ...orig, ...opt };
            diffs[type] = Diff.createPatch(
              TYPE_LABELS[type]?.label || type,
              JSON.stringify(orig, null, 2),
              JSON.stringify(optimizeds[type], null, 2),
              'Original', 'Optimized'
            );
          }
        }
        setGlobalOptimizationResult(originals, optimizeds, diffs,
          result.consistency_report || [], rejectInstructions);
      } else if (originalDiagram) {
        const result = await apiOptimizeUml(originalDiagram, rejectInstructions);
        setOptimizationResult(result.original, result.optimized, result.changes_summary, rejectInstructions);
      }
      // Save the reject review first
      await saveReview({
        action: 'reject',
        comment: rejectInstructions || reviewComment || '(继续优化)',
        requirements: optimizeInstructions,
        original_name: originalDiagram?.name || '',
        optimized_name: optimizedDiagram?.name || '',
        timestamp: new Date().toISOString(),
      });
      setRejectModalVisible(false);
      setResolved(false); // allow new accept/reject on the fresh result
      setReviewComment('');
      message.success({ content: '重新优化完成，请查看新结果', key: 'reoptimize' });
    } catch (e) {
      message.error({ content: '重新优化失败: ' + String(e), key: 'reoptimize' });
    }
    setReoptimizing(false);
  };

  const handleCancelOptimize = async () => {
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
      if (showingOptimized && originalDiagram) {
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
          <p>使用"单图设计"或"全局优化"功能生成设计优化对比</p>
        </Empty>
      </div>
    );
  }

  const buttonsDisabled = resolved && !reoptimizing;

  return (
    <div className="diff-viewer">
      {/* Header with toggle */}
      <div className="diff-header">
        <h3>
          {hasMultiDiagrams
            ? '全局优化对比'
            : (originalDiagram?.diagram_type === 'sequence' ? '时序图优化对比' : 'UML 优化对比')}
        </h3>
        <Button
          icon={<SwapOutlined />}
          size="small"
          type={showingOptimized ? 'primary' : 'default'}
          onClick={handleToggleCanvas}
        >
          {showingOptimized ? '画布: 优化版' : '画布: 原始版'}
        </Button>
      </div>

      {/* Diagram type tabs (shown when multi-diagram data is available) */}
      {hasMultiDiagrams && availableTypes.length > 1 && (
        <div className="diff-type-tabs" style={{ display: 'flex', gap: 4, marginBottom: 8, flexWrap: 'wrap' }}>
          {availableTypes.map(type => {
            const info = TYPE_LABELS[type];
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
                {info?.label || type}
              </Button>
            );
          })}
        </div>
      )}

      {/* Consistency report (global optimization cross-validation findings) */}
      {optimizationConsistencyReport && optimizationConsistencyReport.length > 0 && (
        <div className="diff-summary" style={{ backgroundColor: '#fff7e6', borderLeft: '3px solid #faad14' }}>
          <Tag color="orange">一致性报告</Tag>
          {optimizationConsistencyReport.map((item: any, i: number) => (
            <p key={i} style={{ fontSize: 12, margin: '2px 0' }}>
              {item.severity === 'error' ? '❌' : '⚠️'} {item.msg}
            </p>
          ))}
        </div>
      )}

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
          disabled={resolved}
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
          {resolved ? '已完成评审' : '接受优化（保存评审）'}
        </Button>
        <Button
          danger
          icon={<CloseCircleOutlined />}
          onClick={handleRejectClick}
          loading={false}
          disabled={buttonsDisabled || saving || reoptimizing}
          block
        >
          {resolved ? '已完成评审' : '拒绝优化'}
        </Button>
      </div>
      <div style={{ fontSize: 10, color: '#999', textAlign: 'center', marginTop: 4 }}>
        评审记录将保存在 backend/dev_review.txt
        {resolved && ' | 评审已完成，如需重新优化请点击"单图设计"或"全局优化"按钮'}
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

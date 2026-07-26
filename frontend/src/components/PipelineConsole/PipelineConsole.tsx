/**
 * Pipeline Console – real-time display and control of the 6-stage pipeline.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button, Steps, Tag, message, Alert, Space, Progress, Modal, Input, InputNumber, Tooltip,
} from 'antd';
import {
  PlayCircleOutlined, StopOutlined,
  CheckCircleOutlined, CloseCircleOutlined,
  ReloadOutlined, LoadingOutlined,
  FolderOpenOutlined, CloseOutlined,
} from '@ant-design/icons';
import { useDiagramStore } from '../../stores/diagramStore';
import { useUiStore } from '../../stores/uiStore';
import {
  type PipelineState, type ReActStep, StageStatus, StageName, STAGE_LABELS,
} from '../../types/pipeline';
import {
  createPipelineWs, confirmStage, getPipeline, createPipeline,
  browseDirectory, type BrowseResult,
} from '../../services/api';
import * as Diff from 'diff';
import './PipelineConsole.css';

const PipelineConsole: React.FC = () => {
  const { diagram } = useDiagramStore();
  const {
    selectedLanguage, activePipelineId, setActivePipelineId,
    pipelineSourceDir, pipelineTestDir,
    setPipelineSourceDir, setPipelineTestDir,
  } = useUiStore();

  const [pipeline, setPipeline] = useState<PipelineState | null>(null);
  const [manualRunning, setManualRunning] = useState(false);
  const [wsError, setWsError] = useState<string | null>(null);
  const [currentAction, setCurrentAction] = useState('');
  const testCaseData = useUiStore(s => s.testCaseData);
  const [instructionsVisible, setInstructionsVisible] = useState(false);
  const [pipelineInstructions, setPipelineInstructions] = useState('');
  const [expandedReActStage, setExpandedReActStage] = useState<string | null>(null);
  const [maxChangeRatio, setMaxChangeRatio] = useState(50);  // 0=disabled, default 50%
  const wsRef = useRef<WebSocket | null>(null);

  // ── Directory browsing state ─────────────────────────
  const [sourceBrowseVisible, setSourceBrowseVisible] = useState(false);
  const [testBrowseVisible, setTestBrowseVisible] = useState(false);
  const [browseResult, setBrowseResult] = useState<BrowseResult | null>(null);
  const [browsePath, setBrowsePath] = useState('');
  const [browseTarget, setBrowseTarget] = useState<'source' | 'test'>('source');

  const openBrowse = useCallback(async (target: 'source' | 'test', path?: string) => {
    setBrowseTarget(target);
    try {
      const result = await browseDirectory(path || '', false);  // false = unrestricted browsing
      setBrowseResult(result);
      setBrowsePath(result.current);
      if (target === 'source') setSourceBrowseVisible(true);
      else setTestBrowseVisible(true);
    } catch {
      message.error('加载目录失败');
    }
  }, []);

  const handleBrowseDir = useCallback((dirPath: string) => {
    openBrowse(browseTarget, dirPath);
  }, [openBrowse, browseTarget]);

  const handleBrowseParent = useCallback(() => {
    if (browseResult?.parent) {
      openBrowse(browseTarget, browseResult.parent);
    }
  }, [openBrowse, browseTarget, browseResult]);

  const handleSelectDir = useCallback(() => {
    if (browseTarget === 'source') {
      setPipelineSourceDir(browsePath);
      setSourceBrowseVisible(false);
    } else {
      setPipelineTestDir(browsePath);
      setTestBrowseVisible(false);
    }
  }, [browseTarget, browsePath, setPipelineSourceDir, setPipelineTestDir]);

  // ── Continue with pipeline logic ─────────────────────

  // Derive running/completed from pipeline stage statuses (single source of truth)
  const allStagesTerminal = pipeline?.stages.every(
    s => s.status === StageStatus.SUCCESS || s.status === StageStatus.FAILED || s.status === StageStatus.SKIPPED
  ) ?? false;
  const hasRunningStage = pipeline?.stages.some(
    s => s.status === StageStatus.RUNNING
  ) ?? false;
  const running = manualRunning && (hasRunningStage || !allStagesTerminal);
  // completed stays true once all stages are terminal, unaffected by manualRunning reset
  const showCompleted = allStagesTerminal && (pipeline?.stages.length ?? 0) > 0;

  // Load existing pipeline (only when not running, avoid conflicts)
  useEffect(() => {
    if (activePipelineId && !running) {
      getPipeline(activePipelineId)
        .then(setPipeline)
        .catch(() => { /* Pipeline not created yet, ignore */ });
    }
  }, [activePipelineId, running, setActivePipelineId]);

  // Submit optimization instructions
  const handleSubmitInstructions = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        action: 'submit_instructions',
        instructions: pipelineInstructions,
      }));
      setInstructionsVisible(false);
      setCurrentAction('⏳ 1. UML优化: 正在调用DeepSeek...');
    }
  }, [pipelineInstructions]);

  // Skip instructions (skip Stage 1 entirely)
  const handleSkipInstructions = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        action: 'skip_instructions',
      }));
      setInstructionsVisible(false);
      setCurrentAction('⏭ 1. UML优化: 已跳过 → 进入代码生成...');
    }
  }, []);

  // Stop pipeline
  const handleStop = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'stop' }));
      setCurrentAction('正在停止...');
    }
  }, []);

  // Start pipeline
  const handleStart = useCallback(async () => {
    // Check if any class diagram in the project has content
    const projectDiagrams = useDiagramStore.getState().project.diagrams;
    const classDiagram = projectDiagrams.find(d => (d.diagram_type || 'class') === 'class');
    if (!classDiagram || !classDiagram.classes.length) {
      message.warning('请先在类图中添加至少一个类');
      return;
    }

    setManualRunning(true);
    setWsError(null);
    setCurrentAction('正在创建流水线...');

    try {
      // 1. Create pipeline via REST first
      const created = await createPipeline(diagram.name, diagram);
      const pipeId = created.pipeline_id;
      setActivePipelineId(pipeId);
      setPipeline(created);
      setCurrentAction('正在连接...');

      // 2. Then open WebSocket for real-time updates
      const srcDir = useUiStore.getState().pipelineSourceDir;
      const tstDir = useUiStore.getState().pipelineTestDir;
      const ws = createPipelineWs(pipeId, diagram, selectedLanguage, false, srcDir, tstDir,
        useDiagramStore.getState().project, maxChangeRatio);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.event === 'stage_update') {
          setPipeline(data.data);
          const stageLabel = STAGE_LABELS[data.stage] || data.stage;
          const statusIconMap: Record<string, string> = {
            running: '⏳', success: '✅', failed: '❌', pending: '⏸', skipped: '⏭',
          };
          const statusIcon = statusIconMap[data.status] || '';
          setCurrentAction(`${statusIcon} ${stageLabel}: ${data.logs || data.status}`);

          // When UML optimize succeeds, push result to diff viewer and switch panel
          if (data.stage === 'uml_optimize' && data.status === 'success') {
            const store = useUiStore.getState();
            const result = data.data?.stages?.[0]?.result || {};
            const project = useDiagramStore.getState().project;
            const labelMap: Record<string, string> = { class: '类图', sequence: '时序图', component: '组件图' };

            // ── New format: {diagrams: [{type, name, component_id, data}, ...]} ──
            const diagramsList = result.diagrams;
            if (diagramsList && Array.isArray(diagramsList) && diagramsList.length > 0) {
              // Auto-create/update project diagrams from the LLM result
              useDiagramStore.getState().addDiagramsFromSpec(diagramsList);

              // Build diff data for the DiffViewer
              const originals: Record<string, any> = {};
              const optimizeds: Record<string, any> = {};
              const diffs: Record<string, string> = {};
              const updatedProject = useDiagramStore.getState().project;

              for (const spec of diagramsList) {
                const dkey = `${spec.type}:${spec.name}`;
                // Find the original (pre-optimization) from before the update
                // Use the spec's data as the optimized version
                const orig = project.diagrams.find(
                  d => (d.diagram_type || 'class') === spec.type && d.name === spec.name
                );
                if (orig && spec.data) {
                  originals[dkey] = orig;
                  optimizeds[dkey] = { ...orig, ...spec.data };
                  diffs[dkey] = Diff.createPatch(
                    `${labelMap[spec.type] || spec.type}: ${spec.name}`,
                    JSON.stringify(orig, null, 2),
                    JSON.stringify(optimizeds[dkey], null, 2),
                    'Original', 'Optimized'
                  );
                }
              }

              if (Object.keys(optimizeds).length > 0) {
                store.setGlobalOptimizationResult(
                  originals, optimizeds, diffs,
                  result.consistency_report || [],
                  ''
                );
              }
              store.setRightPanelTab('diff');
              store.setRightPanelVisible(true);
            } else {
              // ── Old format: {optimized: {class, sequence, component}} ──
              const optimizedAll = result.optimized || {};
              if (typeof optimizedAll === 'object' && (optimizedAll.class || optimizedAll.sequence || optimizedAll.component)) {
                const originals: Record<string, any> = {};
                const optimizeds: Record<string, any> = {};
                const diffs: Record<string, string> = {};

                const classOrig = project.diagrams.find(d => (d.diagram_type || 'class') === 'class');
                const seqOrig = project.diagrams.find(d => d.diagram_type === 'sequence');
                const compOrig = project.diagrams.find(d => d.diagram_type === 'component');

                const typeMap: Array<{ key: string; orig: any }> = [
                  { key: 'class', orig: classOrig },
                  { key: 'sequence', orig: seqOrig },
                  { key: 'component', orig: compOrig },
                ];

                const diffLabelMap: Record<string, string> = { class: 'Class Diagram', sequence: 'Sequence Diagram', component: 'Component Diagram' };

                for (const { key, orig } of typeMap) {
                  const opt = optimizedAll[key];
                  if (opt && orig) {
                    originals[key] = orig;
                    optimizeds[key] = { ...orig, ...opt };
                    diffs[key] = Diff.createPatch(
                      diffLabelMap[key] || key,
                      JSON.stringify(orig, null, 2),
                      JSON.stringify(optimizeds[key], null, 2),
                      'Original', 'Optimized'
                    );
                  }
                }

                if (Object.keys(optimizeds).length > 0) {
                  store.setGlobalOptimizationResult(
                    originals, optimizeds, diffs,
                    result.consistency_report || [],
                    ''
                  );
                }
                store.setRightPanelTab('diff');
                store.setRightPanelVisible(true);
              } else {
                // Single diagram optimization (fallback)
                const original = useDiagramStore.getState().diagram;
                const optimized = result.optimized;
                const diff = result.diff || result.changes_summary || '';
                if (original && optimized) {
                  store.setOptimizationResult(original, optimized, diff, '');
                }
                store.setRightPanelTab('diff');
                store.setRightPanelVisible(true);
              }
            }
          }
          // When Case Review stage starts, switch main canvas to test cases
          if (data.stage === 'case_review' && data.status === 'running') {
            const store = useUiStore.getState();
            store.setShowTestCaseInCanvas(true);
          }
        } else if (data.event === 'request_case_review') {
          setCurrentAction('⏸ 等待检视用例完成...');
        } else if (data.event === 'request_instructions') {
          // Stage 1 needs optimization instructions
          setPipelineInstructions('');
          setInstructionsVisible(true);
          setCurrentAction('⏳ 等待输入优化需求...');
        } else if (data.event === 'pipeline_complete') {
          message.success('流水线全部完成！');
          setManualRunning(false);
          setCurrentAction('全部完成');
        } else if (data.event === 'stopped') {
          message.info('流水线已停止');
          setManualRunning(false);
          setCurrentAction('已停止');
          if (data.data) setPipeline(data.data);  // sync terminal state
        } else if (data.event === 'error') {
          setWsError(data.error);
          setManualRunning(false);
          setCurrentAction('错误: ' + data.error);
        }
      };

      ws.onerror = () => {
        setWsError('WebSocket 连接错误');
        setManualRunning(false);
      };

      ws.onclose = () => {
        setManualRunning(false);
        // Don't append "(连接关闭)" if pipeline already completed
        setCurrentAction((prev) => {
          if (prev.includes('全部完成') || prev.includes('已停止')) return prev;
          return prev + ' (连接中断)';
        });
      };
    } catch (e) {
      message.error('创建流水线失败: ' + String(e));
      setManualRunning(false);
      setCurrentAction('');
    }
  }, [diagram, selectedLanguage, activePipelineId, setActivePipelineId]);

  // Confirm case review (Stage 4)
  const handleConfirmCaseReview = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const testCases = useUiStore.getState().testCaseData;
    console.log('[Pipeline] confirm_case_review: testCaseData length =', testCases?.length || 0);
    console.log('[Pipeline] testCaseData preview:', testCases?.substring(0, 300) || '(EMPTY)');
    if (!testCases || testCases.trim().length === 0) {
      message.warning('用例数据为空！请先在主画布中打开测试Excel文件并检视用例，再点击确认。');
      return;
    }
    wsRef.current.send(JSON.stringify({
      action: 'confirm_case_review',
      test_cases: testCases,
    }));
    setCurrentAction('✅ 用例检视确认，继续流水线...');
  }, []);

  // Confirm stage (Stage 2)
  const handleConfirm = useCallback((accepted: boolean) => {
    if (!pipeline || !wsRef.current) return;
    const currentStage = pipeline.current_stage;
    wsRef.current.send(JSON.stringify({
      action: 'confirm',
      stage: currentStage,
      accepted,
      comment: accepted ? 'Accepted' : 'Rejected',
    }));
    confirmStage(pipeline.pipeline_id, currentStage, accepted).catch(console.error);
  }, [pipeline]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  // ── Render helpers ──────────────────────────────────
  const isWaitingConfirm = pipeline?.stages.some(
    (s) => s.name === StageName.DEV_CONFIRM && s.status === StageStatus.RUNNING
  );

  const isWaitingCaseReview = pipeline?.stages.some(
    (s) => s.name === StageName.CASE_REVIEW && s.status === StageStatus.RUNNING
  );

  const stageItems = (pipeline?.stages || []).map((stage) => {
    let status: 'wait' | 'process' | 'finish' | 'error' = 'wait';
    if (stage.status === StageStatus.RUNNING) status = 'process';
    if (stage.status === StageStatus.SUCCESS) status = 'finish';
    if (stage.status === StageStatus.FAILED) status = 'error';

    return {
      title: (
        <span>
          {stage.label || STAGE_LABELS[stage.name] || stage.name}
          {stage.status === StageStatus.RUNNING && (
            <LoadingOutlined style={{ marginLeft: 8, color: '#1890ff' }} spin />
          )}
        </span>
      ),
      description: (
        <div>
          {stage.logs && <span className="stage-logs">{stage.logs}</span>}
          {stage.name === StageName.CODE_GEN && stage.result?.react_steps
            ? (
              <ReActTrace
                steps={stage.result.react_steps as ReActStep[]}
                expanded={expandedReActStage === stage.name}
                onToggle={() => setExpandedReActStage(
                  expandedReActStage === stage.name ? null : stage.name
                )}
              />
            )
            : null}
        </div>
      ),
      status,
    };
  });

  const completedStages = pipeline?.stages.filter(
    (s) => s.status === StageStatus.SUCCESS || s.status === StageStatus.SKIPPED || s.status === StageStatus.FAILED
  ).length || 0;

  const totalStages = pipeline?.stages.length || 6;

  return (
    <div className="pipeline-console">
      <div className="pipeline-header">
        <h3>自动化流水线</h3>
        <Space>
          {!running && !isWaitingConfirm && (
            <>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={handleStart}
              >
                启动流水线
              </Button>
              <Tooltip title="已有项目代码改动上限（%），0=不限">
                <span style={{ fontSize: 11, color: '#999', marginLeft: 8 }}>变更上限</span>
                <InputNumber
                  size="small"
                  min={0} max={100}
                  value={maxChangeRatio}
                  onChange={v => setMaxChangeRatio(v ?? 0)}
                  style={{ width: 65, marginLeft: 4 }}
                  addonAfter="%"
                />
              </Tooltip>
            </>
          )}
          {(running || isWaitingConfirm) && (
            <Button
              danger
              icon={<StopOutlined />}
              onClick={handleStop}
            >
              停止
            </Button>
          )}
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              setActivePipelineId(null);
              setPipeline(null);
              setCurrentAction('');
              setWsError(null);
            }}
            size="small"
            disabled={running}
          />
        </Space>
      </div>

      {wsError && (
        <Alert message={wsError} type="error" closable style={{ marginBottom: 12 }} />
      )}

      {/* Live action display */}
      {currentAction && (
        <div className="pipeline-live-action">
          <LoadingOutlined spin={running} style={{ marginRight: 8 }} />
          <span>{currentAction}</span>
        </div>
      )}

      {pipeline && (
        <>
          <div className="pipeline-progress">
            <Progress
              percent={showCompleted ? 100 : Math.round((completedStages / totalStages) * 100)}
              size="small"
              status={running ? 'active' : showCompleted ? 'success' : undefined}
            />
            <span className="round-info">
              优化轮次: {pipeline.optimization_round}/3
              {running && (
                <Tag color="processing" style={{ marginLeft: 8 }}>运行中</Tag>
              )}
            </span>
          </div>

          <Steps
            direction="vertical"
            size="small"
            current={completedStages}
            items={stageItems}
          />

          {isWaitingConfirm && (
            <div className="confirm-card">
              <p>LLM 已完成 UML 优化，是否接受优化结果？</p>
              <Space>
                <Button type="primary" icon={<CheckCircleOutlined />} onClick={() => handleConfirm(true)}>接受</Button>
                <Button danger icon={<CloseCircleOutlined />} onClick={() => handleConfirm(false)}>拒绝</Button>
              </Space>
            </div>
          )}

          {isWaitingCaseReview && (
            <div className="confirm-card" style={{ background: '#e6f7ff', borderColor: '#91d5ff' }}>
              <p>请在主画布中检视并修改测试用例，完成后点击确认继续</p>
              <p style={{ fontSize: 12, color: testCaseData ? '#52c41a' : '#faad14' }}>
                用例数据: {testCaseData ? `${testCaseData.split('\n').filter(l => l.startsWith('- [')).length} 条用例 ✓` : '等待加载...'}
              </p>
              <Space>
                <Button type="primary" icon={<CheckCircleOutlined />} onClick={handleConfirmCaseReview}>
                  检视完成，继续
                </Button>
              </Space>
            </div>
          )}

          {pipeline.code_artifacts.length > 0 && (() => {
            const srcFiles = [...new Set(pipeline.code_artifacts.filter(a =>
              !a.filename.startsWith('test_') && !a.filename.startsWith('test')
            ).map(a => a.filename))];
            const testFiles = [...new Set(pipeline.code_artifacts.filter(a =>
              a.filename.startsWith('test_') || a.filename.startsWith('test')
            ).map(a => a.filename))];
            return (
              <div className="artifacts-list">
                <h4>代码产物</h4>
                <div style={{ marginBottom: 8 }}>
                  <strong style={{ fontSize: 12 }}>源代码 ({srcFiles.length})：</strong>
                  <div>{srcFiles.map((f, i) => <Tag key={'s'+i} color="blue">{f}</Tag>)}</div>
                </div>
                <div>
                  <strong style={{ fontSize: 12 }}>测试代码 ({testFiles.length})：</strong>
                  <div>
                    {testFiles.map((f, i) => <Tag key={'t'+i} color="green">{f}</Tag>)}
                    {testFiles.length === 0 && <span style={{color:'#999',fontSize:11}}>待生成</span>}
                  </div>
                </div>
              </div>
            );
          })()}

          {pipeline.review_log.length > 0 && (
            <div className="review-log">
              <h4>Review 日志</h4>
              {pipeline.review_log.map((entry, idx) => (
                <div key={idx} className="review-entry">
                  <Tag color={entry.accepted ? 'green' : 'red'}>
                    {entry.accepted ? '接受' : '拒绝'}
                  </Tag>
                  <span>{STAGE_LABELS[entry.stage] || entry.stage}</span>
                  {entry.comment && <span className="review-comment">: {entry.comment}</span>}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ── Directory selection (always visible, persisted to localStorage) ── */}
      {!running && (
        <div style={{ marginBottom: 12, textAlign: 'left', padding: '0 4px' }}>
          <div style={{ marginBottom: 6 }}>
            <label style={{ fontSize: 12, color: '#666', display: 'block', marginBottom: 2 }}>
              源代码目录（可选，留空则从 UML 生成）：
            </label>
            <Input.Group compact>
              <Input
                size="small"
                value={pipelineSourceDir}
                placeholder="选择已有源码目录..."
                readOnly
                style={{ width: 'calc(100% - 64px)' }}
              />
              <Button size="small" icon={<FolderOpenOutlined />} onClick={() => openBrowse('source')} />
              {pipelineSourceDir && (
                <Button size="small" icon={<CloseOutlined />} onClick={() => setPipelineSourceDir('')} />
              )}
            </Input.Group>
            {pipelineSourceDir && (
              <Tag color="blue" style={{ marginTop: 2, fontSize: 11 }}>{pipelineSourceDir}</Tag>
            )}
          </div>
          <div>
            <label style={{ fontSize: 12, color: '#666', display: 'block', marginBottom: 2 }}>
              测试代码目录（可选，留空则从用例生成）：
            </label>
            <Input.Group compact>
              <Input
                size="small"
                value={pipelineTestDir}
                placeholder="选择已有测试目录..."
                readOnly
                style={{ width: 'calc(100% - 64px)' }}
              />
              <Button size="small" icon={<FolderOpenOutlined />} onClick={() => openBrowse('test')} />
              {pipelineTestDir && (
                <Button size="small" icon={<CloseOutlined />} onClick={() => setPipelineTestDir('')} />
              )}
            </Input.Group>
            {pipelineTestDir && (
              <Tag color="green" style={{ marginTop: 2, fontSize: 11 }}>{pipelineTestDir}</Tag>
            )}
          </div>
        </div>
      )}

      {!pipeline && (
        <div className="pipeline-empty">
          <p>点击"启动流水线"开始六阶段自动化流程：</p>
          <ol>
            {Object.entries(STAGE_LABELS).map(([key, label]) => (
              <li key={key}>{label}</li>
            ))}
          </ol>
        </div>
      )}

      {/* ── Directory Browse Modal ─────────────────── */}
      <Modal
        title={browseTarget === 'source' ? '选择源代码目录' : '选择测试代码目录'}
        open={sourceBrowseVisible || testBrowseVisible}
        onCancel={() => {
          setSourceBrowseVisible(false);
          setTestBrowseVisible(false);
        }}
        footer={[
          <Button key="cancel" onClick={() => {
            setSourceBrowseVisible(false);
            setTestBrowseVisible(false);
          }}>
            取消
          </Button>,
          <Button key="select" type="primary" onClick={handleSelectDir}>
            选择此目录
          </Button>,
        ]}
        width={550}
      >
        <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Button size="small" onClick={handleBrowseParent}
            disabled={!browseResult?.parent}>
            上级目录
          </Button>
          <span style={{ fontSize: 12, color: '#666', wordBreak: 'break-all' }}>
            {browseResult?.current || ''}
          </span>
        </div>
        <div style={{ maxHeight: 320, overflow: 'auto' }}>
          {browseResult?.dirs?.map((dir) => (
            <div
              key={dir.path}
              onClick={() => handleBrowseDir(dir.path)}
              style={{ cursor: 'pointer', padding: '4px 8px', borderRadius: 4, display: 'flex', alignItems: 'center', gap: 8 }}
            >
              <FolderOpenOutlined style={{ color: '#faad14' }} />
              <span style={{ fontSize: 13 }}>{dir.name}</span>
            </div>
          ))}
          {(!browseResult?.dirs || browseResult.dirs.length === 0) && (
            <div style={{ color: '#999', fontSize: 12, padding: 8 }}>此目录下无子目录</div>
          )}
        </div>
        {browseResult?.files && browseResult.files.length > 0 && (
          <div style={{ marginTop: 8, fontSize: 11, color: '#888' }}>
            此目录包含 {browseResult.files.length} 个文件（.uml 等）
          </div>
        )}
      </Modal>

      {/* ── Optimization Instructions Modal ──────────── */}
      <Modal
        title="全局设计优化"
        open={instructionsVisible}
        onCancel={handleSkipInstructions}
        onOk={handleSubmitInstructions}
        okText="提交优化"
        cancelText="跳过"
        width={550}
      >
        <p style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>
          当前项目包含
          {(() => {
            const proj = useDiagramStore.getState().project;
            const types = proj.diagrams.map(d => {
              const t = d.diagram_type || 'class';
              const labelMap: Record<string, string> = { class: '类图', sequence: '时序图', component: '组件图' };
              const countMap: Record<string, number> = {
                class: (d.classes || []).length,
                sequence: (d.lifelines || []).length,
                component: (d.components || []).length,
              };
              return `${labelMap[t] || t}(${countMap[t] || 0}元素)`;
            });
            return <span>{types.join('、')}</span>;
          })()}。
          LLM 将交叉校验三种图的一致性并综合优化。
        </p>
        <p style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>
          请输入优化需求（可选）：
        </p>
        <Input.TextArea
          value={pipelineInstructions}
          onChange={(e) => setPipelineInstructions(e.target.value)}
          placeholder={'例如：\n• 检查时序图调用的方法在类图中是否都有定义\n• 优化组件间依赖关系，降低耦合\n• 统一各图的命名规范\n• 补充缺失的接口和交互消息\n• 调整类图结构以匹配时序图交互流程\n...\n留空则进行通用综合优化'}
          rows={5}
          autoFocus
        />
      </Modal>
    </div>
  );
};

// ── Inline ReAct trace component ──────────────────

const ReActTrace: React.FC<{
  steps: ReActStep[];
  expanded: boolean;
  onToggle: () => void;
}> = ({ steps, expanded, onToggle }) => {
  return (
    <div className="react-trace">
      <Button type="link" size="small" onClick={onToggle}>
        {expanded ? '收起推理过程' : '查看推理过程'} ({steps.length} 步)
      </Button>
      {expanded && (
        <div className="react-steps">
          {steps.map((step, i) => {
            const actions = (step.action || '')
              .split(',')
              .map(a => a.trim())
              .filter(Boolean);
            return (
              <div key={i} className="react-step">
                <div className="react-step-header">
                  <Tag color="blue">轮次 {step.round}</Tag>
                  {actions.map((a, j) => (
                    <code key={j}>{a}</code>
                  ))}
                  {step.is_final && <Tag color="green">完成</Tag>}
                </div>
                {step.thought && (
                  <div className="react-thought">
                    <strong>思考:</strong> {step.thought.slice(0, 200)}
                  </div>
                )}
                {step.observation && (
                  <div className="react-observation">
                    <pre>{step.observation}</pre>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default PipelineConsole;

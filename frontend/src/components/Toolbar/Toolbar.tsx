/**
 * Top Toolbar – file operations, LLM actions, view controls.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Button, Select, Tooltip, Dropdown, Modal, List, message, Tag,
  Divider, Input, Form, Slider, Checkbox,
} from 'antd';
import {
  FileAddOutlined, FolderOpenOutlined, SaveOutlined,
  UndoOutlined, RedoOutlined, CodeOutlined, RobotOutlined,
  FileMarkdownOutlined, SettingOutlined, PlayCircleOutlined,
  ZoomInOutlined, ZoomOutOutlined, ExpandOutlined,
  AppstoreOutlined, EyeInvisibleOutlined,
  PlusSquareOutlined, DownOutlined, TableOutlined,
  ProjectOutlined, ApartmentOutlined, ClockCircleOutlined,
  BlockOutlined, MessageOutlined, CloseOutlined,
} from '@ant-design/icons';
import { useDiagramStore } from '../../stores/diagramStore';
import { useUiStore } from '../../stores/uiStore';
import { createDefaultDiagram } from '../../types/uml';
import {
  saveDiagram, openDiagram, listDiagrams,
  saveProject, openProject, listProjects,
  exportMarkdown, generateCode as apiGenerateCode,
  optimizeUml as apiOptimizeUml, createPipeline,
  browseDirectory, type BrowseResult,
  saveGeneratedCode,
} from '../../services/api';
import { handleDesignElement, processDesignUpdated } from '../../services/designElementHandler';
import './Toolbar.css';

// 占位回调：Toolbar 用它来预先建立 WebSocket 连接，
// (reserved for future use)

const { TextArea } = Input;

const LANGUAGES = [
  { value: 'python', label: 'Python' },
  { value: 'java', label: 'Java' },
  { value: 'typescript', label: 'TypeScript' },
  { value: 'javascript', label: 'JavaScript' },
  { value: 'csharp', label: 'C#' },
  { value: 'cpp', label: 'C++' },
  { value: 'go', label: 'Go' },
  { value: 'rust', label: 'Rust' },
  { value: 'ruby', label: 'Ruby' },
  { value: 'swift', label: 'Swift' },
  { value: 'kotlin', label: 'Kotlin' },
  { value: 'php', label: 'PHP' },
];

const Toolbar: React.FC = () => {
  const {
    diagram, project, isModified, undoStack, redoStack,
    undo, redo, setProject, newProject, setActiveDiagram, addDiagram,
    removeDiagram,
    toggleGrid, setGridSize, setGridColor, setGridThickness,
    setCurrentFilepath, currentFilepath,
  } = useDiagramStore();

  const {
    selectedLanguage,
    setSelectedLanguage, setGeneratedCode, setRightPanelTab,
    setRightPanelVisible, setCodeGenLoading,
    setOptimizationResult,
    setActivePipelineId, fileDialogVisible, setFileDialogVisible,
    showTestCaseInCanvas, toggleTestCaseInCanvas,
    agentChatVisible, setAgentChatVisible,
    pipelineSourceDir, pipelineTestDir,
    setPipelineSourceDir, setPipelineTestDir,
  } = useUiStore();

  const [fileList, setFileList] = useState<Array<{
    name: string; path: string; size: number; modified: string;
  }>>([]);

  // ── Path input for open dialog ──────────────────────
  const [pathInput, setPathInput] = useState('');

  // ── Quick-access paths ──────────────────────────────
  const userProfile = (() => {
    // Try to detect home directory from common env patterns
    // Vite exposes env vars via import.meta.env; also try USERPROFILE (Windows)
    const env = (typeof import.meta !== 'undefined' && (import.meta as any).env) || {};
    const up = env.VITE_USERPROFILE || '';
    if (up) return up;
    // Fallback: try common drives for Windows
    return 'C:/Users';
  })();

  const QUICK_PATHS = [
    { label: '📂 桌面', path: `${userProfile}/Desktop` },
    { label: '📂 文档', path: `${userProfile}/Documents` },
    { label: '🏠 用户', path: userProfile },
    { label: '💾 C盘', path: 'C:/' },
    { label: '💾 D盘', path: 'D:/' },
  ];

  // ── Save As dialog ──────────────────────────────────
  const [saveAsVisible, setSaveAsVisible] = useState(false);
  const [saveFilename, setSaveFilename] = useState('');
  const [saving, setSaving] = useState(false);

  // ── Optimize dialog ─────────────────────────────────
  const [optimizeVisible, setOptimizeVisible] = useState(false);
  const [optimizeInstructions, setOptimizeInstructions] = useState('');
  const [optimizing, setOptimizing] = useState(false);
  const [gridSettingsVisible, setGridSettingsVisible] = useState(false);
  const [globalOptimizeVisible, setGlobalOptimizeVisible] = useState(false);
  const [globalInstructions, setGlobalInstructions] = useState('');
  const [globalOptimizing, setGlobalOptimizing] = useState(false);
  const [globalStreamMode, setGlobalStreamMode] = useState(false);

  // ── Global optimize handler (unified: via Agent WebSocket) ─────────
  const handleGlobalOptimize = async () => {
    const proj = useDiagramStore.getState().project;

    setGlobalOptimizing(true);
    setGlobalOptimizeVisible(false);
    message.loading({ content: globalStreamMode ? '流式优化中，实时生成设计...' : '全局优化中...', key: 'globalOpt', duration: 0 });

    const uiState = useUiStore.getState();

    // ── v2: 连接专用 optimize_v2 WebSocket ──
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = (import.meta as any).env?.VITE_API_TOKEN as string | undefined;
    const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';
    const wsUrl = `${protocol}//${window.location.host}/api/optimize_v2/ws${tokenParam}`;

    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      ws.send(JSON.stringify({
        project_file: currentFilepath || '',
        instructions: globalInstructions.trim(),
        stream_mode: globalStreamMode,
      }));
      // 显示优化画布（不打开 AgentChat 面板）
      if (!uiState.rightPanelVisible) {
        uiState.setRightPanelVisible(true);
      }
    };

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.event === 'design_element') {
          // 流式模式: 实时渲染元素到画布
          handleDesignElement(useDiagramStore.getState(), { type: data.type, data: data.data });
        } else if (data.event === 'design_updated') {
          const diagrams = data.diagrams;
          if (Array.isArray(diagrams) && diagrams.length > 0) {
            processDesignUpdated(
              diagrams,
              data.consistency_report || [],
              useUiStore.getState(),
              useDiagramStore.getState(),
            );
          }
          message.success({ content: '全局优化完成，请审核变更', key: 'globalOpt' });
          ws.close();
        } else if (data.event === 'error') {
          message.error({ content: `优化失败: ${data.message}`, key: 'globalOpt' });
          ws.close();
        }
      } catch { /* ignore parse errors */ }
    };

    ws.onerror = () => {
      message.error({ content: '优化连接失败，请确认后端已启动', key: 'globalOpt' });
      setGlobalOptimizing(false);
    };

    ws.onclose = () => {
      setGlobalOptimizing(false);
    };
  };

  // ── Ctrl+S global save ──────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.ctrlKey && e.key === 's') {
        e.preventDefault();
        handleSave();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [currentFilepath, project, isModified]); // eslint-disable-line

  // ── File operations ─────────────────────────────────
  const handleNew = () => {
    const doNew = () => {
      newProject();
      message.success('已创建新项目');
    };
    if (isModified) {
      Modal.confirm({
        title: '未保存的更改',
        content: '当前项目有未保存的更改，确定要新建吗？',
        onOk: doNew,
        okText: '确定新建',
        cancelText: '取消',
      });
    } else {
      doNew();
    }
  };

  const [browseData, setBrowseData] = useState<BrowseResult | null>(null);
  const [currentBrowsePath, setCurrentBrowsePath] = useState('');
  const browseUnsafe = useRef(false);  // track whether we're browsing outside project
  const currentFileSafe = useRef(true);  // safe flag matching the currently opened file

  // ── Project directory selection (for AI Agent & Pipeline) ──
  const [projDirBrowseVisible, setProjDirBrowseVisible] = useState(false);
  const [projDirBrowseTarget, setProjDirBrowseTarget] = useState<'source' | 'test'>('source');
  const [projDirBrowseResult, setProjDirBrowseResult] = useState<BrowseResult | null>(null);
  const [projDirBrowsePath, setProjDirBrowsePath] = useState('');

  const handleBrowseDirFor = useCallback(async (target: 'source' | 'test', path?: string) => {
    setProjDirBrowseTarget(target);
    try {
      // 默认从当前设置的值或默认路径开始浏览
      const initialPath = path || (
        target === 'source'
          ? (pipelineSourceDir || 'D:/AI_tools/uml_designer/generated/source')
          : (pipelineTestDir || 'D:/AI_tools/uml_designer/generated/test')
      );
      const result = await browseDirectory(initialPath, false);
      setProjDirBrowseResult(result);
      setProjDirBrowsePath(result.current);
      setProjDirBrowseVisible(true);
    } catch {
      message.error('加载目录失败');
    }
  }, []);

  const handleProjDirSelect = useCallback((dirPath: string) => {
    if (projDirBrowseTarget === 'source') {
      setPipelineSourceDir(dirPath);
    } else {
      setPipelineTestDir(dirPath);
    }
    setProjDirBrowseVisible(false);
    message.success(`已设置${projDirBrowseTarget === 'source' ? '源码' : '测试'}目录: ${dirPath}`);
  }, [projDirBrowseTarget, setPipelineSourceDir, setPipelineTestDir]);

  const handleProjDirNav = useCallback((dirPath: string) => {
    handleBrowseDirFor(projDirBrowseTarget, dirPath);
  }, [handleBrowseDirFor, projDirBrowseTarget]);

  const handleProjDirParent = useCallback(() => {
    if (projDirBrowseResult?.parent) {
      handleBrowseDirFor(projDirBrowseTarget, projDirBrowseResult.parent);
    }
  }, [handleBrowseDirFor, projDirBrowseTarget, projDirBrowseResult]);

  const handleOpen = async (path?: string, forceUnsafe = false) => {
    setFileDialogVisible(true);
    try {
      const safe = !forceUnsafe && !browseUnsafe.current;
      const result = await browseDirectory(path || currentBrowsePath || '', safe);
      setBrowseData(result);
      setCurrentBrowsePath(result.current);
      setPathInput(result.current);
    } catch {
      message.error('加载文件列表失败');
    }
  };

  const handleNavigateTo = (targetPath: string) => {
    browseUnsafe.current = true;
    setPathInput(targetPath);
    handleOpen(targetPath, true);
  };

  const handleBrowseDir = (dirPath: string) => {
    handleOpen(dirPath, browseUnsafe.current);
  };

  const handleBrowseParent = () => {
    if (browseData?.parent) {
      handleOpen(browseData.parent, browseUnsafe.current);
    }
  };

  const handleOpenFile = async (path: string, isProject: boolean) => {
    try {
      if (isProject) {
        const safe = !browseUnsafe.current;
        const proj = await openProject(path, safe);
        setProject(proj);
        setCurrentFilepath(path);
        currentFileSafe.current = safe;
        setFileDialogVisible(false);
        message.success(`项目已打开: ${proj.name} (${proj.diagrams.length} 张图)`);
      } else {
        const safe = !browseUnsafe.current;
        const d = await openDiagram(path, safe);
        // Wrap single .uml diagram in a fresh Project so stale
        // sequence/component entries from the previous project are cleared.
        const proj = {
          version: '1.0',
          name: d.name,
          diagrams: [d],
          active_diagram_index: 0,
        };
        setProject(proj);
        setCurrentFilepath(path);
        currentFileSafe.current = safe;
        setFileDialogVisible(false);
        message.success('文件已打开');
      }
    } catch {
      message.error('打开文件失败');
    }
  };

  // Quick save (always saves as .umlproj project)
  const handleSave = async () => {
    if (!currentFilepath) {
      openSaveAs();
      return;
    }
    try {
      // 若工程名仍是默认值，则用当前文件路径的文件名同步工程名
      let proj = project;
      const curName = currentFilepath.replace(/[\\/]+/g, '/').split('/').pop() || '';
      const curBase = curName.replace(/\.umlproj$/i, '').replace(/\.uml$/i, '');
      if (!proj.name || proj.name === 'Untitled') {
        proj = { ...proj, name: curBase };
        setProject(proj);
      }
      const result = await saveProject(proj, currentFilepath, currentFileSafe.current);
      setCurrentFilepath(result.filepath);
      message.success(`项目已保存: ${result.filename}`);
    } catch {
      message.error('保存失败');
    }
  };

  // Open Save As dialog
  const openSaveAs = async () => {
    setSaveFilename(project.name || 'Untitled');
    try {
      const result = await browseDirectory('');
      setBrowseData(result);
      setFileList(result.files || []);
    } catch { /* ignore */ }
    setSaveAsVisible(true);
  };

  const handleSaveAs = async () => {
    setSaving(true);
    try {
      const fname = saveFilename.trim() || project.name || 'Untitled';
      // 同步工程名到 store，使 .umlproj 文件与知识图谱 project 节点使用一致的名字
      const projName = fname.replace(/\.umlproj$/i, '');
      if (projName !== project.name) {
        setProject({ ...project, name: projName });
      }
      // Always save as project (.umlproj)
      const result = await saveProject({ ...project, name: projName }, fname);
      setCurrentFilepath(result.filepath);
      setSaveAsVisible(false);
      message.success(`项目已保存: ${result.filename}`);
    } catch {
      message.error('保存失败');
    }
    setSaving(false);
  };

  const handleExportMd = async () => {
    try {
      const md = await exportMarkdown(diagram);
      const blob = new Blob([md], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${diagram.name}_design.md`;
      a.click();
      URL.revokeObjectURL(url);
      message.success('Markdown 文档已导出');
    } catch {
      message.error('导出失败');
    }
  };

  // ── LLM operations ──────────────────────────────────
  const handleGenerateCode = async () => {
    const dt = diagram.diagram_type || 'class';
    if (dt !== 'class') {
      message.warning('代码生成目前仅支持类图');
      return;
    }
    setCodeGenLoading(true);
    message.loading({ content: '正在生成代码...', key: 'codegen' });
    try {
      const result = await apiGenerateCode(diagram, selectedLanguage);
      setGeneratedCode(result.files);
      setRightPanelTab('code');
      setRightPanelVisible(true);
      saveGeneratedCode({
        project_name: diagram.name, language: selectedLanguage,
        source_files: result.files, test_files: {},
      }).catch(() => {});
      message.success({ content: `已生成 ${Object.keys(result.files).length} 个文件 → generated/src`, key: 'codegen', duration: 5 });
    } catch (e) {
      message.error({ content: '代码生成失败: ' + String(e), key: 'codegen' });
    }
    setCodeGenLoading(false);
  };

  // Open optimize dialog first, then send to LLM
  const handleOptimizeClick = () => {
    setOptimizeInstructions('');
    setOptimizeVisible(true);
  };

  const handleOptimizeConfirm = async () => {
    setOptimizing(true);
    const dt = diagram.diagram_type || 'class';
    const loadText = dt === 'sequence' ? 'LLM 正在分析优化时序图...' : dt === 'component' ? 'LLM 正在分析优化组件图...' : 'LLM 正在单图优化...';
    message.loading({ content: loadText, key: 'optimize' });
    try {
      const result = await apiOptimizeUml(diagram, optimizeInstructions);
      setOptimizationResult(result.original, result.optimized, result.changes_summary, optimizeInstructions);
      setRightPanelTab('diff');
      setRightPanelVisible(true);
      setOptimizeVisible(false);
      message.success({ content: 'UML 优化完成，请在对比面板查看', key: 'optimize' });
    } catch (e) {
      message.error({ content: 'UML 优化失败: ' + String(e), key: 'optimize' });
    }
    setOptimizing(false);
  };

  const handleStartPipeline = async () => {
    const projectDiagrams = useDiagramStore.getState().project.diagrams;
    const classDiagram = projectDiagrams.find(d => (d.diagram_type || 'class') === 'class');
    if (!classDiagram || !classDiagram.classes.length) {
      message.warning('请先在类图中添加至少一个类');
      return;
    }
    try {
      const pipeline = await createPipeline(diagram.name, diagram);
      setActivePipelineId(pipeline.pipeline_id);
      setRightPanelTab('pipeline');
      setRightPanelVisible(true);
      message.info('流水线已创建，请在流水线面板中启动');
    } catch (e) {
      message.error('创建流水线失败: ' + String(e));
    }
  };

  // ── View controls ───────────────────────────────────
  const handleZoomIn = () => useDiagramStore.getState().setZoom(diagram.zoom * 1.2);
  const handleZoomOut = () => useDiagramStore.getState().setZoom(diagram.zoom / 1.2);
  const handleZoomReset = () => useDiagramStore.getState().setZoom(1.0);

  const saveMenuItems = [
    { key: 'save', label: `保存${isModified ? ' ●' : ''}`, onClick: handleSave },
    { key: 'saveas', label: '另存为...', onClick: openSaveAs },
  ];

  return (
    <div className="toolbar">
      {/* Row 1: File + Diagrams + Undo/Redo + LLM */}
      <div className="toolbar-row">
      <div className="toolbar-left">
        {/* File Ops */}
        <Tooltip title="新建">
          <Button icon={<FileAddOutlined />} onClick={handleNew} />
        </Tooltip>
        <Tooltip title="打开">
          <Button icon={<FolderOpenOutlined />} onClick={() => handleOpen()} />
        </Tooltip>

        <Dropdown menu={{ items: saveMenuItems }} trigger={['click']}>
          <Button icon={<SaveOutlined />}>
            {isModified ? '● ' : ''}保存 <DownOutlined />
          </Button>
        </Dropdown>

        <Divider type="vertical" />

        {/* Diagram dropdowns — grouped by type */}
        {(() => {
          const TYPE_SPECS = [
            { key: 'component', label: '组件图', icon: <BlockOutlined />, color: '#d48806' },
            { key: 'class', label: '类图', icon: <ApartmentOutlined />, color: '#1677ff' },
            { key: 'sequence', label: '时序图', icon: <ClockCircleOutlined />, color: '#52c41a' },
          ] as const;

          const compDiag = project.diagrams.find((dd) => dd.diagram_type === 'component');
          const activeIdx = project.active_diagram_index;

          const handleDelete = (index: number, name: string) => {
            Modal.confirm({
              title: `删除「${name}」`,
              content: '确认删除此图？此操作不可撤销。',
              okText: '删除', okType: 'danger', cancelText: '取消',
              onOk: () => removeDiagram(index),
            });
          };

          return TYPE_SPECS.map(spec => {
            const items = project.diagrams
              .map((d, i) => ({ d, i }))
              .filter(({ d }) => (d.diagram_type || 'class') === spec.key);

            if (items.length === 0) return null;

            const activeItem = items.find(({ i }) => i === activeIdx);
            const displayLabel = activeItem
              ? (() => {
                  const d = activeItem.d;
                  const isAuto = !d.name || d.name === 'Untitled' || /^(class|sequence|component)_\d+$/.test(d.name);
                  const parentComp = d.component_id
                    ? (compDiag?.components || []).find((c) => c.id === d.component_id)
                    : null;
                  const base = isAuto ? spec.label : d.name;
                  return parentComp ? `${parentComp.name} › ${base}` : base;
                })()
              : `${spec.label} (${items.length})`;

            const menuItems = items.map(({ d, i }) => {
              const isActive = i === activeIdx;
              const isAuto = !d.name || d.name === 'Untitled' || /^(class|sequence|component)_\d+$/.test(d.name);
              const parentComp = d.component_id
                ? (compDiag?.components || []).find((c) => c.id === d.component_id)
                : null;
              const itemLabel = isAuto ? spec.label : d.name;
              const fullLabel = parentComp ? `${parentComp.name} › ${itemLabel}` : itemLabel;
              return {
                key: String(i),
                icon: isActive ? <span style={{ color: spec.color, fontWeight: 'bold' }}>✔</span> : <span style={{ width: 14, display: 'inline-block' }} />,
                label: (
                  <span style={{
                    display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center', minWidth: 180, gap: 8,
                    fontWeight: isActive ? 600 : 400,
                    color: isActive ? spec.color : 'inherit',
                  }}>
                    <span style={{
                      overflow: 'hidden', textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap', maxWidth: 200,
                    }}>{fullLabel}</span>
                    <span
                      style={{ cursor: 'pointer', color: '#999', fontSize: 12, flexShrink: 0 }}
                      onClick={(e) => { e.stopPropagation(); handleDelete(i, fullLabel); }}
                      title="删除此图"
                    >🗑</span>
                  </span>
                ),
                onClick: () => setActiveDiagram(i),
              };
            });

            return (
              <Dropdown key={spec.key} menu={{ items: menuItems }} trigger={['click']}>
                <Button
                  type={activeItem ? 'primary' : 'default'}
                  icon={spec.icon}
                  style={{
                    marginRight: 2, maxWidth: 200,
                    borderColor: activeItem ? spec.color : undefined,
                    color: activeItem ? spec.color : undefined,
                  }}
                >
                  <span style={{
                    overflow: 'hidden', textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap', display: 'inline-block', maxWidth: 150,
                  }}>{displayLabel}</span>
                  <DownOutlined style={{ fontSize: 10, marginLeft: 4 }} />
                </Button>
              </Dropdown>
            );
          });
        })()}

        <Tooltip title="添加新图">
          <Dropdown menu={{
            items: [
              { key: 'class', label: '添加类图', icon: <ApartmentOutlined />,
                onClick: () => addDiagram('class') },
              { key: 'sequence', label: '添加时序图', icon: <ClockCircleOutlined />,
                onClick: () => addDiagram('sequence') },
              { key: 'component', label: '添加组件图', icon: <BlockOutlined />,
                onClick: () => addDiagram('component') },
            ],
          }} trigger={['click']}>
            <Button icon={<PlusSquareOutlined />} />
          </Dropdown>
        </Tooltip>

        <Divider type="vertical" />

        {/* Undo/Redo */}
        <Tooltip title="撤销 Ctrl+Z">
          <Button icon={<UndoOutlined />} disabled={undoStack.length === 0} onClick={undo} />
        </Tooltip>
        <Tooltip title="重做 Ctrl+Y">
          <Button icon={<RedoOutlined />} disabled={redoStack.length === 0} onClick={redo} />
        </Tooltip>
      </div>
      <div className="toolbar-right" />
      </div>

      {/* Row 2: Project directories (global — shared by Agent, Pipeline, etc.) */}
      <div className="toolbar-row" style={{ padding: '2px 0' }}>
        <div className="toolbar-left" style={{ gap: 10, flexWrap: 'wrap', width: '100%' }}>
          <Tooltip title="选择已有项目源码目录后，AI 助手和流水线将基于已有代码进行增量开发。留空则从 UML 设计全新生成代码。">
            <Tag
              icon={<FolderOpenOutlined />}
              color={pipelineSourceDir ? 'blue' : 'default'}
              style={{ cursor: 'pointer', margin: 0, fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}
              onClick={() => handleBrowseDirFor('source')}
            >
              源码目录{pipelineSourceDir ? `: ${pipelineSourceDir.split(/[/\\]/).slice(-2).join('/')}` : ''}
            </Tag>
          </Tooltip>
          {pipelineSourceDir && (
            <Tooltip title="清除源码目录">
              <Button size="small" type="text" icon={<CloseOutlined />} onClick={() => setPipelineSourceDir('')} style={{ padding: 0, minWidth: 18, height: 18 }} />
            </Tooltip>
          )}
          <Tooltip title="选择已有测试代码目录后，AI 助手和流水线将运行已有测试而非从零生成。留空则自动生成 pytest 测试。">
            <Tag
              icon={<FolderOpenOutlined />}
              color={pipelineTestDir ? 'green' : 'default'}
              style={{ cursor: 'pointer', margin: 0, fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}
              onClick={() => handleBrowseDirFor('test')}
            >
              测试目录{pipelineTestDir ? `: ${pipelineTestDir.split(/[/\\]/).slice(-2).join('/')}` : ''}
            </Tag>
          </Tooltip>
          {pipelineTestDir && (
            <Tooltip title="清除测试目录">
              <Button size="small" type="text" icon={<CloseOutlined />} onClick={() => setPipelineTestDir('')} style={{ padding: 0, minWidth: 18, height: 18 }} />
            </Tooltip>
          )}
        </div>
      </div>

      {/* Row 3: LLM + Export + View */}
      <div className="toolbar-row">
      <div className="toolbar-left">
        {/* LLM */}
        <Select
          value={selectedLanguage}
          onChange={setSelectedLanguage}
          style={{ width: 110 }}
          options={LANGUAGES}
          size="small"
        />
        <Tooltip title="LLM 生成代码">
          <Button icon={<CodeOutlined />} onClick={handleGenerateCode}>
            生成代码
          </Button>
        </Tooltip>
        <Tooltip title="LLM 单图优化（仅优化当前图，弹窗收集需求）">
          <Button icon={<RobotOutlined />} onClick={handleOptimizeClick}>
            单图设计
          </Button>
        </Tooltip>
        <Tooltip title="全局综合优化（类图+时序图+组件图交叉验证，也支持从需求描述直接生成全部图）">
          <Button icon={<RobotOutlined />} onClick={() => setGlobalOptimizeVisible(true)} style={{ color: '#722ed1' }}>
            全局优化
          </Button>
        </Tooltip>
        <Tooltip title="启动自动化流水线">
          <Button icon={<PlayCircleOutlined />} onClick={handleStartPipeline}>
            流水线
          </Button>
        </Tooltip>
        <Tooltip title="AI 开发助手（对话驱动开发）">
          <Button
            icon={<MessageOutlined />}
            onClick={() => setAgentChatVisible(true)}
            type={agentChatVisible ? 'primary' : 'default'}
            style={agentChatVisible ? { color: '#fff', borderColor: '#1677ff', background: '#1677ff' } : {}}
          >
            AI 助手
          </Button>
        </Tooltip>

        <Divider type="vertical" />

        {/* Export */}
        <Tooltip title="导出 Markdown 设计文档">
          <Button icon={<FileMarkdownOutlined />} onClick={handleExportMd}>
            导出MD
          </Button>
        </Tooltip>
      </div>

      <div className="toolbar-right">
        <Tooltip title="显示/隐藏网格">
          <Button
            icon={diagram.grid_visible ? <AppstoreOutlined /> : <EyeInvisibleOutlined />}
            onClick={toggleGrid}
          />
        </Tooltip>

        <Tooltip title="网格设置">
          <Button
            icon={<SettingOutlined />}
            onClick={() => setGridSettingsVisible(true)}
          >
            {diagram.grid_size}px
          </Button>
        </Tooltip>

        <Divider type="vertical" />

        <Tooltip title={showTestCaseInCanvas ? '返回UML画布' : '用例检视'}>
          <Button
            icon={<TableOutlined />}
            type={showTestCaseInCanvas ? 'primary' : 'default'}
            onClick={toggleTestCaseInCanvas}
          >
            用例
          </Button>
        </Tooltip>

        <Tooltip title="缩小">
          <Button icon={<ZoomOutOutlined />} onClick={handleZoomOut} />
        </Tooltip>
        <span className="zoom-label">{Math.round(diagram.zoom * 100)}%</span>
        <Tooltip title="放大">
          <Button icon={<ZoomInOutlined />} onClick={handleZoomIn} />
        </Tooltip>
        <Tooltip title="重置缩放">
          <Button icon={<ExpandOutlined />} onClick={handleZoomReset} />
        </Tooltip>
      </div>
      </div>

      {/* ── File Open Dialog with folder browsing ────── */}
      <Modal
        title="打开 UML 文件"
        open={fileDialogVisible}
        onCancel={() => { setFileDialogVisible(false); browseUnsafe.current = false; }}
        footer={null}
        width={650}
      >
        {/* Breadcrumb / navigation */}
        <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Button size="small" onClick={handleBrowseParent}
            disabled={!browseData?.parent}>
            上级目录
          </Button>
          <span style={{ fontSize: 12, color: '#666', wordBreak: 'break-all', flex: 1 }}>
            {browseData?.current || ''}
          </span>
        </div>

        {/* Path input + Go button */}
        <div style={{ marginBottom: 8, display: 'flex', gap: 8 }}>
          <Input
            size="small"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            onPressEnter={() => handleNavigateTo(pathInput)}
            placeholder="输入或粘贴目录路径，按回车跳转..."
            style={{ flex: 1 }}
            allowClear
          />
          <Button
            size="small"
            type="primary"
            onClick={() => handleNavigateTo(pathInput)}
          >
            跳转
          </Button>
        </div>

        {/* Quick-access paths */}
        <div style={{ marginBottom: 10, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {QUICK_PATHS.map((qp) => (
            <Button
              key={qp.path}
              size="small"
              onClick={() => handleNavigateTo(qp.path)}
              style={{ fontSize: 11 }}
            >
              {qp.label}
            </Button>
          ))}
        </div>

        <List
          loading={false}
          locale={{ emptyText: '暂无保存的文件' }}
          size="small"
          style={{ maxHeight: 400, overflow: 'auto' }}
        >
          {/* Directories first */}
          {browseData?.dirs?.map((dir) => (
            <List.Item
              key={dir.path}
              onClick={() => handleBrowseDir(dir.path)}
              style={{ cursor: 'pointer', background: '#fafafa' }}
            >
              <List.Item.Meta
                avatar={<FolderOpenOutlined style={{ fontSize: 18, color: '#faad14' }} />}
                title={<span style={{ fontSize: 13 }}>📁 {dir.name}</span>}
              />
            </List.Item>
          ))}
          {/* Project files (.umlproj) — shown first */}
          {browseData?.files?.filter(f => f.type === 'project').map((item) => (
            <List.Item
              key={item.path}
              onClick={() => handleOpenFile(item.path, true)}
              style={{ cursor: 'pointer', background: '#f0f5ff' }}
            >
              <List.Item.Meta
                avatar={<ProjectOutlined style={{ fontSize: 18, color: '#1890ff' }} />}
                title={<span style={{ fontSize: 13 }}>📦 {item.name}</span>}
                description={
                  <span style={{ fontSize: 11 }}>
                    {new Date(item.modified).toLocaleString()} | {(item.size / 1024).toFixed(1)} KB
                  </span>
                }
              />
            </List.Item>
          ))}
          {/* Single diagram files (.uml) */}
          {browseData?.files?.filter(f => f.type !== 'project').map((item) => (
            <List.Item
              key={item.path}
              onClick={() => handleOpenFile(item.path, false)}
              style={{ cursor: 'pointer' }}
            >
              <List.Item.Meta
                title={<span style={{ fontSize: 13 }}>📄 {item.name}</span>}
                description={
                  <span style={{ fontSize: 11 }}>
                    {new Date(item.modified).toLocaleString()} | {(item.size / 1024).toFixed(1)} KB
                  </span>
                }
              />
            </List.Item>
          ))}
        </List>
      </Modal>

      {/* ── Save As Dialog ───────────────────────────── */}
      <Modal
        title="另存为"
        open={saveAsVisible}
        onCancel={() => setSaveAsVisible(false)}
        onOk={handleSaveAs}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={550}
      >
        <Form layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="文件名 (.umlproj)">
            <Input
              value={saveFilename}
              onChange={(e) => setSaveFilename(e.target.value)}
              suffix=".umlproj"
              placeholder="输入项目名称..."
              autoFocus
            />
          </Form.Item>
        </Form>

        <Divider orientation="left" plain style={{ fontSize: 12 }}>
          已有项目文件（保存在 {currentFilepath || 'uml_files/'}）
        </Divider>

        <List
          loading={false}
          dataSource={fileList.slice(0, 8)}
          locale={{ emptyText: '暂无已保存的项目' }}
          size="small"
          renderItem={(item) => (
            <List.Item
              onClick={() => setSaveFilename(item.name.replace('.umlproj', '').replace('.uml', ''))}
              style={{ cursor: 'pointer' }}
            >
              <List.Item.Meta
                title={<span style={{ fontSize: 12 }}>{item.name}</span>}
                description={
                  <span style={{ fontSize: 11 }}>
                    {new Date(item.modified).toLocaleString()} | {(item.size / 1024).toFixed(1)} KB
                  </span>
                }
              />
            </List.Item>
          )}
        />
      </Modal>

      {/* ── Single-Diagram Optimize Dialog ──────────────────── */}
      <Modal
        title={(diagram.diagram_type === 'sequence' ? '时序图' : diagram.diagram_type === 'component' ? '组件图' : '类图') + '单图' + (diagram.classes.length || (diagram.lifelines || []).length || (diagram.components || []).length ? '优化' : '生成')}
        open={optimizeVisible}
        onCancel={() => setOptimizeVisible(false)}
        onOk={handleOptimizeConfirm}
        confirmLoading={optimizing}
        okText="提交优化"
        cancelText="取消"
        width={600}
      >
        <p style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>
          {diagram.diagram_type === 'sequence'
            ? <>当前时序图包含 <strong>{(diagram.lifelines || []).length}</strong> 个生命线，<strong>{(diagram.messages || []).length}</strong> 条消息。</>
            : diagram.diagram_type === 'component'
            ? <>当前组件图包含 <strong>{(diagram.components || []).length}</strong> 个组件，<strong>{(diagram.comp_relations || []).length}</strong> 条依赖。</>
            : <>当前类图包含 <strong>{diagram.classes.length}</strong> 个类，<strong>{diagram.relations.length}</strong> 条关系。</>
          }
          请输入你的优化需求，LLM 将结合当前设计和你的需求进行优化：
        </p>
        <TextArea
          value={optimizeInstructions}
          onChange={(e) => setOptimizeInstructions(e.target.value)}
          placeholder={diagram.diagram_type === 'sequence'
            ? '例如：\n• 为OtaTask和CrowTask之间增加异常处理消息\n• 补充缺失的返回消息\n• 调整消息调用顺序使其更合理\n• 为关键消息添加功能备注\n...'
            : diagram.diagram_type === 'component'
            ? '例如：\n• 将AuthService拆分为AuthProvider和TokenManager\n• 为DataModule补充ILogger依赖接口\n• 检查组件间的循环依赖\n• 为PaymentGateway增加提供的IPayment接口\n...'
            : '例如：\n• 将User和Order改为聚合关系\n• 为Payment添加refund方法\n• 提取公共接口IPayable\n• 优化类的职责划分，减少耦合\n• 应用工厂模式改造创建逻辑\n...'}
          rows={6}
          autoFocus
        />
      </Modal>

      {/* ── Grid Settings Modal ──────────────────────── */}
      <Modal
        title="网格设置"
        open={gridSettingsVisible}
        onCancel={() => setGridSettingsVisible(false)}
        onOk={() => setGridSettingsVisible(false)}
        okText="确定"
        cancelText="取消"
        width={420}
      >
        <Form layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item label="网格大小">
            <Select
              value={diagram.grid_size}
              onChange={(v) => setGridSize(v)}
              options={[
                { value: 5, label: '5px' },
                { value: 10, label: '10px' },
                { value: 20, label: '20px' },
                { value: 50, label: '50px' },
              ]}
            />
          </Form.Item>

          <Form.Item label="线条颜色">
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="color"
                value={diagram.grid_color || '#e0e0e0'}
                onChange={(e) => setGridColor(e.target.value)}
                style={{ width: 40, height: 32, border: '1px solid #d9d9d9', borderRadius: 4, cursor: 'pointer' }}
              />
              <Input
                value={diagram.grid_color || '#e0e0e0'}
                onChange={(e) => setGridColor(e.target.value)}
                style={{ width: 100 }}
                placeholder="#e0e0e0"
              />
              <span style={{ fontSize: 12, color: '#888' }}>选择或输入颜色</span>
            </div>
          </Form.Item>

          <Form.Item label="线条粗细">
            <Slider
              min={1}
              max={5}
              value={diagram.grid_thickness || 1}
              onChange={(v) => setGridThickness(v)}
              marks={{ 1: '细', 3: '中', 5: '粗' }}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* ── Global Optimize Modal ──────────────────── */}
      <Modal
        title="全局综合优化"
        open={globalOptimizeVisible}
        onCancel={() => setGlobalOptimizeVisible(false)}
        onOk={handleGlobalOptimize}
        confirmLoading={globalOptimizing}
        okText="提交优化"
        cancelText="取消"
        width={650}
      >
        <p style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>
          LLM 将同时分析项目中的类图、时序图、组件图，进行交叉一致性校验和综合优化。
          {(() => {
            const proj = useDiagramStore.getState().project;
            const types = proj.diagrams.map(d => d.diagram_type === 'sequence' ? '时序图' : d.diagram_type === 'component' ? '组件图' : '类图');
            return <>当前项目包含：{types.join('、')}</>;
          })()}
        </p>
        <Checkbox
          checked={globalStreamMode}
          onChange={(e) => setGlobalStreamMode(e.target.checked)}
          style={{ marginBottom: 8 }}
        >
          动态绘图（勾选后实时生成到画布，实验性功能）
        </Checkbox>
        <Input.TextArea
          value={globalInstructions}
          onChange={(e) => setGlobalInstructions(e.target.value)}
          placeholder={'输入全局优化需求，如：\n• 检查时序图引用的方法是否在类图中都有定义\n• 优化组件间依赖关系\n• 统一命名规范\n• 补充缺失的接口定义\n留空则进行通用综合优化'}
          rows={6}
          autoFocus
        />
      </Modal>

      {/* ── Project Directory Browse Modal ─────────── */}
      <Modal
        title={`选择${projDirBrowseTarget === 'source' ? '源码' : '测试'}目录`}
        open={projDirBrowseVisible}
        onCancel={() => setProjDirBrowseVisible(false)}
        footer={null}
        width={600}
      >
        <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Button size="small" onClick={handleProjDirParent}
            disabled={!projDirBrowseResult?.parent}>
            上级目录
          </Button>
          <span style={{ fontSize: 12, color: '#666', wordBreak: 'break-all', flex: 1 }}>
            {projDirBrowseResult?.current || ''}
          </span>
        </div>
        <div style={{ marginBottom: 8, display: 'flex', gap: 8 }}>
          <Input
            size="small"
            value={projDirBrowsePath}
            onChange={(e) => setProjDirBrowsePath(e.target.value)}
            onPressEnter={() => handleProjDirNav(projDirBrowsePath)}
            placeholder="输入目录路径，按回车跳转..."
            style={{ flex: 1 }}
            allowClear
          />
          <Button size="small" type="primary" onClick={() => handleProjDirNav(projDirBrowsePath)}>
            跳转
          </Button>
        </div>
        <div style={{ marginBottom: 10, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {QUICK_PATHS.map((qp) => (
            <Button key={qp.path} size="small" onClick={() => handleProjDirNav(qp.path)} style={{ fontSize: 11 }}>
              {qp.label}
            </Button>
          ))}
        </div>
        <div style={{ marginBottom: 8 }}>
          <Button
            type="primary"
            size="small"
            icon={<FolderOpenOutlined />}
            onClick={() => handleProjDirSelect(projDirBrowseResult?.current || projDirBrowsePath)}
          >
            选择当前目录
          </Button>
        </div>
        <List
          size="small"
          style={{ maxHeight: 300, overflow: 'auto' }}
          locale={{ emptyText: '暂无子目录' }}
        >
          {projDirBrowseResult?.dirs?.map((dir) => (
            <List.Item
              key={dir.path}
              onClick={() => handleProjDirSelect(dir.path)}
              style={{ cursor: 'pointer' }}
            >
              <List.Item.Meta
                avatar={<FolderOpenOutlined style={{ fontSize: 16, color: '#faad14' }} />}
                title={<span style={{ fontSize: 13 }}>{dir.name}</span>}
              />
            </List.Item>
          ))}
        </List>
      </Modal>

    </div>
  );
};

export default Toolbar;

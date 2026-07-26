/**
 * Top Toolbar – file operations, LLM actions, view controls.
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  Button, Select, Tooltip, Dropdown, Modal, List, message,
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
  BlockOutlined,
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
  saveGeneratedCode, optimizeProject as apiOptimizeProject,
} from '../../services/api';
import './Toolbar.css';

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

  // Diagram tab right-click menu
  const [tabCtxMenu, setTabCtxMenu] = useState<{
    visible: boolean; x: number; y: number; index: number;
  }>({ visible: false, x: 0, y: 0, index: -1 });

  // ── Global optimize handler (complete mode) ─────────
  const handleGlobalOptimize = async () => {
    const proj = useDiagramStore.getState().project;

    // Build existing_diagrams list (new format) + keep old fields for backward compat
    const existingDiagrams = proj.diagrams.map(d => ({
      type: d.diagram_type || 'class',
      name: d.name,
      component_id: d.component_id || '',
      data: d,
    }));
    const classD = proj.diagrams.find(d => (d.diagram_type || 'class') === 'class');
    const seqD = proj.diagrams.find(d => d.diagram_type === 'sequence');
    const compD = proj.diagrams.find(d => d.diagram_type === 'component');

    if (globalStreamMode) {
      // ── Streaming mode ──────────────────────────────
      setGlobalOptimizing(true);
      setGlobalOptimizeVisible(false);
      message.loading({ content: '流式优化中，实时生成设计...', key: 'globalOpt', duration: 0 });
      try {
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        const token = (import.meta as any).env?.VITE_API_TOKEN;
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const resp = await fetch('/api/llm/optimize-project-stream', {
          method: 'POST', headers,
          body: JSON.stringify({
            instructions: globalInstructions,
            existing_diagrams: existingDiagrams,
            class_diagram: classD || {}, sequence_diagram: seqD || {}, component_diagram: compD || {},
          }),
        });
        await handleStreamResponse(resp, proj);
        useDiagramStore.getState().triggerRecenter();
        message.success({ content: '流式优化完成', key: 'globalOpt' });
      } catch (e) {
        message.error({ content: '流式优化失败: ' + String(e), key: 'globalOpt' });
      }
      setGlobalOptimizing(false);
    } else {
      // ── Complete mode ────────────────────────────────
      setGlobalOptimizing(true);
      setGlobalOptimizeVisible(false);
      message.loading({ content: '全局优化中...', key: 'globalOpt', duration: 0 });
      try {
        const result: any = await apiOptimizeProject({
          instructions: globalInstructions,
          existing_diagrams: existingDiagrams,
          class_diagram: classD as any, sequence_diagram: seqD as any,
          component_diagram: compD as any,
        });
        // New format: {diagrams: [...]} — auto-create/update
        const specs = result?.diagrams || [];
        if (specs.length > 0) {
          useDiagramStore.getState().addDiagramsFromSpec(specs);
          message.success({ content: `全局优化完成，已生成/更新 ${specs.length} 张图`, key: 'globalOpt' });
        } else {
          // Old format fallback: {optimized: {class, sequence, component}}
          const optimized = result?.optimized as any;
          if (optimized) {
            const store = useDiagramStore.getState();
            const diagrams = [...store.project.diagrams];
            for (const dtype of ['class', 'sequence', 'component'] as const) {
              if (optimized[dtype]) {
                const idx = diagrams.findIndex(d =>
                  (d.diagram_type || 'class') === dtype);
                if (idx >= 0) {
                  diagrams[idx] = { ...diagrams[idx], ...optimized[dtype] };
                } else {
                  const newD = createDefaultDiagram(optimized[dtype].name || dtype);
                  newD.diagram_type = dtype;
                  newD.component_id = optimized[dtype].component_id || '';
                  diagrams.push({ ...newD, ...optimized[dtype] });
                }
              }
            }
            store.setProject({ ...store.project, diagrams });
          }
          message.success({ content: '全局优化完成，请查看各图', key: 'globalOpt' });
        }
      } catch (e) {
        message.error({ content: '全局优化失败: ' + String(e), key: 'globalOpt' });
      }
      setGlobalOptimizing(false);
    }
  };

  // ── Streaming handler (incremental element-by-element) ─
  const handleStreamResponse = async (resp: Response, proj: { diagrams: Array<{ diagram_type?: string; name?: string; component_id?: string }> }) => {
      const reader = resp.body?.getReader();
      if (!reader) throw new Error('No stream');
      const decoder = new TextDecoder();

      const idMap = new Map<string, string>();
      const getMapped = (llmId: string) => idMap.get(llmId) || llmId;
      let currentType = '';

      const switchTo = (type: string) => {
        if (currentType === type) return;
        let idx = type === 'class' ? proj.diagrams.findIndex(d => (d.diagram_type || 'class') === 'class')
          : type === 'sequence' ? proj.diagrams.findIndex(d => d.diagram_type === 'sequence')
          : proj.diagrams.findIndex(d => d.diagram_type === 'component');
        if (idx < 0) {
          // Auto-create missing diagram type so streaming elements have a target
          const autoName = type === 'class' ? '类图' : type === 'sequence' ? '时序图' : '组件图';
          useDiagramStore.getState().addDiagram(type, autoName);
          idx = useDiagramStore.getState().project.diagrams.length - 1;
        }
        if (idx >= 0) { useDiagramStore.getState().setActiveDiagram(idx); currentType = type; }
      };

      /** Get the last item from an array (most recently added). */
      function lastOf<T>(arr: T[] | undefined): T | null { return arr && arr.length > 0 ? arr[arr.length - 1] : null; }

      // ── Element dispatch table ────────────────────────
      const handlers: Record<string, (obj: any) => void> = {
        diagram_create: (obj) => {
          // New diagram entry detected in the stream — auto-create the tab
          const dtype = obj.type || 'class';
          const dname = obj.name || dtype;
          const cid = obj.component_id || '';
          let idx = proj.diagrams.findIndex(
            d => (d.diagram_type || 'class') === dtype && d.name === dname
          );
          if (idx < 0) {
            useDiagramStore.getState().addDiagram(dtype, dname, cid);
            idx = useDiagramStore.getState().project.diagrams.length - 1;
          }
          useDiagramStore.getState().setActiveDiagram(idx);
          currentType = dtype;
        },
        class: (obj) => {
          switchTo('class');
          const s = useDiagramStore.getState();
          const pos = obj.position || { x: 100, y: 100 };
          s.addClass({ x: pos.x, y: pos.y });
          const cls = lastOf(useDiagramStore.getState().diagram.classes);
          if (cls) {
            idMap.set(obj.id, cls.id);
            s.updateClass(cls.id, {
              name: obj.name || 'Class', stereotype: obj.stereotype || 'class',
              attributes: obj.attributes || [], methods: obj.methods || [],
              note: obj.note || '',
              provided_interfaces: obj.provided_interfaces || [],
              required_interfaces: obj.required_interfaces || [],
            });
            if (obj.size) s.updateClass(cls.id, { size: obj.size } as any);
          }
        },
        relation: (obj) => {
          switchTo('class');
          useDiagramStore.getState().addRelation(getMapped(obj.source), getMapped(obj.target));
          const rel = lastOf(useDiagramStore.getState().diagram.relations);
          if (rel) {
            useDiagramStore.getState().updateRelation(rel.id, {
              type: obj.type || 'association',
              multiplicity_source: obj.multiplicity_source || '',
              multiplicity_target: obj.multiplicity_target || '',
              role_name: obj.role_name || '', note: obj.note || '',
            });
          }
        },
        lifeline: (obj) => {
          switchTo('sequence');
          useDiagramStore.getState().addLifeline(obj.x ?? 200);
          const ll = lastOf(useDiagramStore.getState().diagram.lifelines);
          if (ll) {
            idMap.set(obj.id, ll.id);
            useDiagramStore.getState().updateLifeline(ll.id, {
              name: obj.name || 'Participant', class_ref: obj.class_ref || '',
              activations: obj.activations || [],
            });
          }
        },
        message: (obj) => {
          switchTo('sequence');
          useDiagramStore.getState().addMessage(getMapped(obj.from_lifeline), getMapped(obj.to_lifeline));
          const msg = lastOf(useDiagramStore.getState().diagram.messages);
          if (msg) {
            useDiagramStore.getState().updateMessage(msg.id, {
              label: obj.label || 'message()', type: obj.type || 'sync',
              order: obj.order || msg.order, note: obj.note || '', y: obj.y,
            });
          }
        },
        fragment: (obj) => {
          switchTo('sequence');
          useDiagramStore.getState().addFragment(obj.y_start ?? 200);
          const frag = lastOf(useDiagramStore.getState().diagram.fragments);
          if (frag) {
            useDiagramStore.getState().updateFragment(frag.id, {
              type: obj.type || 'loop', label: obj.label || '',
              x: obj.x ?? 80, width: obj.width ?? 280,
              y_start: obj.y_start ?? 200, y_end: obj.y_end ?? 320,
            } as any);
          }
        },
        component: (obj) => {
          switchTo('component');
          useDiagramStore.getState().addComponent({ x: obj.x ?? 150, y: obj.y ?? 100 }, obj.parent_id || '');
          const comp = lastOf(useDiagramStore.getState().diagram.components);
          if (comp) {
            idMap.set(obj.id, comp.id);
            useDiagramStore.getState().updateComponent(comp.id, {
              name: obj.name || 'Component', width: obj.width ?? 200, height: obj.height ?? 160,
              provided_interfaces: obj.provided_interfaces || [],
              required_interfaces: obj.required_interfaces || [],
            });
          }
        },
        comp_rel: (obj) => {
          switchTo('component');
          useDiagramStore.getState().addCompRelation(getMapped(obj.source), getMapped(obj.target));
          const crel = lastOf(useDiagramStore.getState().diagram.comp_relations);
          if (crel) {
            useDiagramStore.getState().updateCompRelation(crel.id, { type: obj.type || 'dependency' });
          }
        },
        diagram_meta: (obj) => {
          const dtype = obj.diagram_type || 'class';
          switchTo(dtype);
          // Set component_id on the active diagram
          const store = useDiagramStore.getState();
          const idx = store.project.active_diagram_index;
          const diag = store.project.diagrams[idx];
          if (diag && obj.component_id && !diag.component_id) {
            const newDiagrams = store.project.diagrams.map((d, i) =>
              i === idx ? { ...d, component_id: obj.component_id } : d
            );
            store.setProject({ ...store.project, diagrams: newDiagrams });
          }
        },
      };

      // ── SSE read loop ────────────────────────────────
      let receivedBytes = 0, sseMsgCount = 0;
      let textBuffer = '', currentData = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) { console.log('[Stream] Reader done, total bytes:', receivedBytes, 'msgs:', sseMsgCount); break; }
        receivedBytes += value?.length || 0;
        textBuffer += decoder.decode(value, { stream: true });

        while (true) {
          const nlIdx = textBuffer.indexOf('\n');
          if (nlIdx < 0) break;
          const rawLine = textBuffer.slice(0, nlIdx);
          textBuffer = textBuffer.slice(nlIdx + 1);

          if (rawLine.startsWith('data: ')) {
            currentData += rawLine.slice(6);
          } else if (rawLine === '') {
            if (!currentData) continue;
            sseMsgCount++;
            console.log('[Stream] SSE msg #' + sseMsgCount + ':', currentData.slice(0, 150));
            if (currentData === 'DONE') {
              console.log('[Stream] DONE received, total msgs:', sseMsgCount);
              useDiagramStore.getState().triggerRecenter();
              return;
            }
            const colonIdx = currentData.indexOf(':');
            if (colonIdx >= 0) {
              const elemType = currentData.slice(0, colonIdx);
              const jsonStr = currentData.slice(colonIdx + 1);
              try {
                const obj = JSON.parse(jsonStr);
                console.log('[Stream] Element:', elemType, obj.name || obj.label || obj.id);
                handlers[elemType]?.(obj);
              } catch (e) {
                console.warn('[Stream] JSON parse failed for', elemType + ':', (e as Error).message, 'json:', jsonStr.slice(0, 100));
              }
            }
            currentData = '';
          }
        }
      }
  }; // handleStreamResponse

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
      const result = await saveProject(project, currentFilepath, currentFileSafe.current);
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
      // Always save as project (.umlproj)
      const result = await saveProject(project, fname);
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

        {/* Diagram tabs — switch between diagrams */}
        {project.diagrams.map((d, i) => {
          const isActive = i === project.active_diagram_index;
          const type = d.diagram_type || 'class';
          const icon = type === 'sequence'
            ? <ClockCircleOutlined />
            : type === 'component'
            ? <BlockOutlined />
            : <ApartmentOutlined />;
          const typeLabel = type === 'sequence' ? '时序图' : type === 'component' ? '组件图' : '类图';
          // Only show custom name if user explicitly renamed it (not auto-generated pattern)
          const isAutoName = !d.name || d.name === 'Untitled' || /^(class|sequence|component)_\d+$/.test(d.name);
          // Find parent component name if linked
          const compDiag = project.diagrams.find((dd) => dd.diagram_type === 'component');
          const parentComp = d.component_id
            ? (compDiag?.components || []).find((c) => c.id === d.component_id)
            : null;
          const baseLabel = isAutoName ? typeLabel : d.name;
          const label = parentComp ? `${parentComp.name} › ${baseLabel}` : baseLabel;
          const tip = isAutoName
            ? `${typeLabel}（${d.name}）${parentComp ? ` — 属于组件「${parentComp.name}」` : ''}`
            : `${d.name}（${typeLabel}）${parentComp ? ` — 属于组件「${parentComp.name}」` : ''}`;
          return (
            <Tooltip key={i} title={tip}>
              <Button
                type={isActive ? 'primary' : 'default'}
                icon={icon}
                onClick={() => setActiveDiagram(i)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setTabCtxMenu({ visible: true, x: e.clientX, y: e.clientY, index: i });
                }}
                style={{ marginRight: 2, maxWidth: 180 }}
                title={label}
              >
                <span style={{
                  overflow: 'hidden', textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap', display: 'inline-block', maxWidth: 140,
                }}>{label}</span>
              </Button>
            </Tooltip>
          );
        })}

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

      {/* Row 2: LLM + Export + View */}
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

      {/* Diagram tab right-click context menu */}
      {tabCtxMenu.visible && (() => {
        const diag = project.diagrams[tabCtxMenu.index];
        if (!diag) return null;
        const dtype = diag.diagram_type || 'class';
        const typeLabel = dtype === 'sequence' ? '时序图' : dtype === 'component' ? '组件图' : '类图';
        const closeMenu = () => setTabCtxMenu((p) => ({ ...p, visible: false }));
        return (
          <>
            <div style={{ position: 'fixed', inset: 0, zIndex: 999 }} onClick={closeMenu} />
            <div style={{
              position: 'fixed', left: tabCtxMenu.x, top: tabCtxMenu.y, zIndex: 1000,
              background: '#fff', border: '1px solid #d9d9d9', borderRadius: 8,
              boxShadow: '0 4px 16px rgba(0,0,0,0.15)', padding: 4, minWidth: 160,
            }}>
              <div style={{
                padding: '6px 12px', fontSize: 13, fontWeight: 600,
                color: '#555', borderBottom: '1px solid #f0f0f0', marginBottom: 4,
              }}>
                {typeLabel}：{diag.name}
              </div>
              <div style={{
                padding: '5px 12px', cursor: 'pointer',
                fontSize: 12, borderRadius: 4, display: 'flex', alignItems: 'center', gap: 6,
                color: '#ff4d4f',
              }}
                onMouseEnter={(e) => { e.currentTarget.style.background = '#fff2f0'; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                onClick={() => {
                  Modal.confirm({
                    title: `删除「${diag.name}」`,
                    content: `确认删除此${typeLabel}？此操作不可撤销。`,
                    okText: '删除', okType: 'danger', cancelText: '取消',
                    onOk: () => removeDiagram(tabCtxMenu.index),
                  });
                  closeMenu();
                }}
              >
                <span>🗑️</span> <span>删除此图</span>
              </div>
            </div>
          </>
        );
      })()}
    </div>
  );
};

export default Toolbar;

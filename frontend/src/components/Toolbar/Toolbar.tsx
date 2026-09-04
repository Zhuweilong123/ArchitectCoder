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
  UndoOutlined, RedoOutlined, RobotOutlined,
  FileMarkdownOutlined, SettingOutlined,
  ZoomInOutlined, ZoomOutOutlined, ExpandOutlined,
  AppstoreOutlined, EyeInvisibleOutlined,
  PlusSquareOutlined, DownOutlined, TableOutlined,
  ProjectOutlined, ApartmentOutlined, ClockCircleOutlined,
  BlockOutlined, MessageOutlined, CloseOutlined, HistoryOutlined, LineChartOutlined,
} from '@ant-design/icons';
import { selectActiveDiagram, useDiagramStore } from '../../stores/diagramStore';
import { useShallow } from 'zustand/react/shallow';
import { useUiStore } from '../../stores/uiStore';
import { createDefaultDiagram } from '../../types/uml';
import {
  saveDiagram, openDiagram, listDiagrams,
  saveProject, openProject, listProjects,
  exportMarkdown,
  browseDirectory, type BrowseResult,
} from '../../services/api';
import { handleDesignElement, processDesignUpdated } from '../../services/designElementHandler';
import './Toolbar.css';
import { t, type TranslationKey } from '../../i18n';
import SettingsPopover from '../Settings/SettingsPopover';

// 占位回调：Toolbar 用它来预先建立 WebSocket 连接，
// (reserved for future use)

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
    markSaved,
    toggleGrid, setGridSize, setGridColor, setGridThickness,
    setCurrentFilepath, currentFilepath, currentWorkspacePath,
    currentWorkspaceSafe, setCurrentWorkspacePath,
  } = useDiagramStore(useShallow((s) => ({
    diagram: selectActiveDiagram(s),
    project: s.project,
    isModified: s.isModified,
    undoStack: s.undoStack,
    redoStack: s.redoStack,
    undo: s.undo,
    redo: s.redo,
    setProject: s.setProject,
    newProject: s.newProject,
    setActiveDiagram: s.setActiveDiagram,
    addDiagram: s.addDiagram,
    removeDiagram: s.removeDiagram,
    markSaved: s.markSaved,
    toggleGrid: s.toggleGrid,
    setGridSize: s.setGridSize,
    setGridColor: s.setGridColor,
    setGridThickness: s.setGridThickness,
    setCurrentFilepath: s.setCurrentFilepath,
    currentFilepath: s.currentFilepath,
    currentWorkspacePath: s.currentWorkspacePath,
    currentWorkspaceSafe: s.currentWorkspaceSafe,
    setCurrentWorkspacePath: s.setCurrentWorkspacePath,
  })));
  const viewport = useDiagramStore((s) => s.viewport);

  const {
    selectedLanguage,
    setSelectedLanguage,
    fileDialogVisible, setFileDialogVisible,
    showTestCaseInCanvas, toggleTestCaseInCanvas,
    agentChatVisible, setAgentChatVisible,
    projectRoot, sourceDir, testDir, interfaceLanguage,
    setProjectRoot, setSourceDir, setTestDir, setTraceVisible, setEvaluationVisible,
  } = useUiStore();
  const copy = (key: TranslationKey) => t(interfaceLanguage, key);

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

  const localizedQuickPath = (label: string) => interfaceLanguage === 'en'
    ? label.replace('桌面', 'Desktop').replace('文档', 'Documents').replace('用户', 'User').replace('盘', ' Drive')
    : label;

  // Keep the toolbar compact while still showing where the active design is
  // located.  Paths are displayed relative to the workspace's parent; the
  // full host path remains available in the tooltip.
  const normalizePath = (value: string) => value.replace(/\\/g, '/').replace(/\/+$/, '');
  const pathBaseName = (value: string) => normalizePath(value).split('/').pop() || value;
  const fileStem = (value: string) =>
    (pathBaseName(value).replace(/[^\w.-]/g, '_').replace(/^\.+|\.+$/g, '') || 'Untitled');
  const pathDirName = (value: string) => {
    const normalized = normalizePath(value);
    const index = normalized.lastIndexOf('/');
    return index > 0 ? normalized.slice(0, index) : normalized;
  };
  const relativePath = (value: string, root: string) => {
    const target = normalizePath(value);
    const base = normalizePath(root);
    if (target === base) return '.';
    const prefix = `${base}/`;
    return target.startsWith(prefix) ? target.slice(prefix.length) : pathBaseName(target);
  };
  // ── Save As dialog ──────────────────────────────────
  const [saveAsVisible, setSaveAsVisible] = useState(false);
  const [saveFilename, setSaveFilename] = useState('');
  const [saving, setSaving] = useState(false);

  // ── Optimize dialog ─────────────────────────────────
  const [gridSettingsVisible, setGridSettingsVisible] = useState(false);
  const [globalOptimizeVisible, setGlobalOptimizeVisible] = useState(false);
  const [globalInstructions, setGlobalInstructions] = useState('');
  const [globalOptimizing, setGlobalOptimizing] = useState(false);
  const [globalStreamMode, setGlobalStreamMode] = useState(true);

  // ── Global optimize handler (SSE 流式 / REST 非流式) ─────────
  const handleGlobalOptimize = async () => {
    const proj = useDiagramStore.getState().project;

    setGlobalOptimizing(true);
    setGlobalOptimizeVisible(false);
    const loadText = globalStreamMode ? '正在分析影响范围...' : '全局优化中...';
    message.loading({ content: loadText, key: 'globalOpt', duration: 0 });

    const uiState = useUiStore.getState();

    // ── 空 project 时自动另存为获取文件路径 ──
    let projectFile = currentFilepath;
    if (!projectFile) {
      try {
        const projName = useDiagramStore.getState().project?.name || 'Untitled';
        const targetPath = currentWorkspacePath
          ? `${normalizePath(currentWorkspacePath)}/${fileStem(projName)}.umlproj`
          : `${projName}.umlproj`;
        const result = await saveProject(
          { ...useDiagramStore.getState().project, name: projName },
          targetPath,
          currentWorkspacePath ? currentWorkspaceSafe : true,
        );
        projectFile = result.filepath;
        setCurrentFilepath(result.filepath);
        markSaved(result.revision);
      } catch {
        message.error({ content: '优化前需要先保存项目文件', key: 'globalOpt' });
        setGlobalOptimizing(false);
        return;
      }
    }

    const token = (import.meta as any).env?.VITE_API_TOKEN as string | undefined;
    useDiagramStore.getState().beginBatch();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    if (!globalStreamMode) {
      // ── 非流式: REST API ──
      try {
        const response = await fetch('/api/optimize_v2/optimize', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            project_file: projectFile || '',
            instructions: globalInstructions.trim(),
          }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();
        const diagrams = result.diagrams;
        if (Array.isArray(diagrams) && diagrams.length > 0) {
          processDesignUpdated(
            diagrams,
            result.consistency_report || [],
            useUiStore.getState(),
            useDiagramStore.getState(),
          );
        }
        message.success({ content: '全局优化完成，请审核变更', key: 'globalOpt' });
      } catch {
        message.error({ content: '优化连接失败，请确认后端已启动', key: 'globalOpt' });
      } finally {
        useDiagramStore.getState().endBatch();
        setGlobalOptimizing(false);
      }
      return;
    }

    // ── 流式: SSE fetch + ReadableStream ──
    const idMap = new Map<string, string>();
    const clearedDiagrams = new Set<string>();   // 记录流式已清空旧数据的图
    const abortController = new AbortController();

    // 保存原始图快照（供 diff 对比用，流式阶段 store 会被覆盖）
    const originalsSnapshot: Record<string, any> = {};
    for (const d of proj.diagrams) {
      const dkey = `${d.diagram_type || 'class'}:${d.name}`;
      if (Object.keys(d).length > 1) {  // >1 排除仅含 name/type 的默认空图
        originalsSnapshot[dkey] = { ...d };
      }
    }

    try {
      const response = await fetch('/api/optimize_v2/stream', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          project_file: projectFile || '',
          instructions: globalInstructions.trim(),
        }),
        signal: abortController.signal,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';

      // 显示优化画布
      if (!uiState.rightPanelVisible) {
        uiState.setRightPanelVisible(true);
      }

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6);
          if (!payload) continue;

          if (payload === 'DONE') {
            // 流式元素结束，等待 design_updated
          } else if (payload.startsWith('status:')) {
            // Phase 1 完成：显示影响范围摘要
            try {
              const status = JSON.parse(payload.slice(7));
              message.loading({ content: status.message, key: 'globalOpt', duration: 0 });
            } catch { /* ignore */ }
          } else if (payload.startsWith('error:')) {
            try {
              const err = JSON.parse(payload.slice(6));
              message.error({ content: `优化失败: ${err.message}`, key: 'globalOpt' });
            } catch {
              message.error({ content: '优化失败', key: 'globalOpt' });
            }
          } else if (payload.startsWith('design_updated:')) {
            try {
              const data = JSON.parse(payload.slice(15)); // "design_updated:".length === 15
              const diagrams = data.diagrams;
              if (Array.isArray(diagrams) && diagrams.length > 0) {
                processDesignUpdated(
                  diagrams,
                  data.consistency_report || [],
                  useUiStore.getState(),
                  useDiagramStore.getState(),
                  originalsSnapshot,
                );
              }
              message.success({ content: '全局优化完成，请审核变更', key: 'globalOpt' });
            } catch { /* ignore parse errors */ }
          } else {
            // 设计元素: <type>:<json>
            const colonIdx = payload.indexOf(':');
            if (colonIdx > 0) {
              const elemType = payload.slice(0, colonIdx);
              const elemData = payload.slice(colonIdx + 1);
              handleDesignElement(
                useDiagramStore.getState(),
                { type: elemType, data: elemData },
                idMap,
                clearedDiagrams,
              );
            }
          }
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        message.error({ content: '优化连接失败，请确认后端已启动', key: 'globalOpt' });
      }
    } finally {
      useDiagramStore.getState().endBatch();
      setGlobalOptimizing(false);
    }
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

  // ── Project directory selection (for AI Agent) ──
  const [projDirBrowseVisible, setProjDirBrowseVisible] = useState(false);
  const [projDirBrowseTarget, setProjDirBrowseTarget] = useState<'project' | 'source' | 'test'>('project');
  const [projDirBrowseResult, setProjDirBrowseResult] = useState<BrowseResult | null>(null);
  const [projDirBrowsePath, setProjDirBrowsePath] = useState('');

  const handleBrowseDirFor = useCallback(async (target: 'project' | 'source' | 'test', path?: string) => {
    setProjDirBrowseTarget(target);
    try {
      // 默认从当前设置的值开始浏览，无设置则用当前工作目录
      const initialPath = path || (
        target === 'project'
          ? (projectRoot || 'project')
          : target === 'source'
            ? (sourceDir || '.')
            : (testDir || '.')
      );
      const result = await browseDirectory(initialPath, false);
      setProjDirBrowseResult(result);
      setProjDirBrowsePath(result.current);
      setProjDirBrowseVisible(true);
    } catch {
      message.error('加载目录失败');
    }
  }, [projectRoot, sourceDir, testDir]);

  const handleProjDirSelect = useCallback((dirPath: string) => {
    if (projDirBrowseTarget === 'project') {
      void handleProjectFolderSelect(dirPath);
      return;
    }
    if (projDirBrowseTarget === 'source') {
      setSourceDir(dirPath);
    } else {
      setTestDir(dirPath);
    }
    setProjDirBrowseVisible(false);
    message.success(`已设置${projDirBrowseTarget === 'source' ? '源码' : '测试'}目录: ${dirPath}`);
  }, [projDirBrowseTarget, setSourceDir, setTestDir]);

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

  const handleBrowseDesign = () => {
    const designPath = projectRoot
      ? `${normalizePath(projectRoot)}/design`
      : (currentFilepath ? pathDirName(currentFilepath) : '');
    void handleOpen(designPath || undefined, true);
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

  const handleSelectCurrentFolder = async () => {
    const folder = browseData?.current;
    if (!folder) return;
    const childDirNames = new Set((browseData?.dirs || []).map((item) => item.name.toLowerCase()));
    if (['design', 'src', 'test'].some((name) => childDirNames.has(name))) {
      await handleProjectFolderSelect(folder);
      return;
    }
    const safe = !browseUnsafe.current;
    const projectFiles = (browseData.files || []).filter((item) => item.type === 'project');
    const diagramFiles = (browseData.files || []).filter((item) =>
      item.type !== 'project' && item.name.toLowerCase().endsWith('.uml'),
    );
    const designFiles = [...projectFiles, ...diagramFiles];

    // Selecting a folder changes the save target.  An existing open file is
    // therefore detached until the folder's design is loaded below.
    setCurrentWorkspacePath(folder, safe);
    currentFileSafe.current = safe;
    setCurrentFilepath(null);

    if (projectFiles.length > 0) {
      // A .umlproj is already the canonical multi-diagram container. Open the
      // first one deterministically; any additional project files remain
      // available from the folder browser.
      await handleOpenFile(projectFiles[0].path, true);
      if (projectFiles.length > 1) {
        message.info(`\u5df2\u81ea\u52a8\u6253\u5f00 ${projectFiles[0].name}\uff0c\u76ee\u5f55\u4e2d\u8fd8\u6709 ${projectFiles.length - 1} \u4e2a\u9879\u76ee\u6587\u4ef6`);
      }
      return;
    }

    if (diagramFiles.length === 1) {
      await handleOpenFile(diagramFiles[0].path, false);
      return;
    }

    if (diagramFiles.length > 1) {
      try {
        const diagrams = await Promise.all(
          diagramFiles.map((item) => openDiagram(item.path, safe)),
        );
        setProject({
          version: '1.0',
          name: pathBaseName(folder) || 'Untitled',
          diagrams,
          active_diagram_index: 0,
        });
        // There is no single source file for an aggregated project; save it as
        // a new .umlproj in the selected folder on the next save.
        setCurrentFilepath(null);
        setCurrentWorkspacePath(folder, safe);
        setFileDialogVisible(false);
        message.success(`\u5df2\u81ea\u52a8\u52a0\u8f7d ${diagrams.length} \u4e2a UML \u6587\u4ef6`);
      } catch {
        message.error('\u52a0\u8f7d\u6587\u4ef6\u5939\u4e2d\u7684 UML \u6587\u4ef6\u5931\u8d25');
      }
      return;
    }

    // An empty directory is a valid new-design workspace.
    if (designFiles.length === 0) {
      newProject(pathBaseName(folder) || 'Untitled');
      setCurrentWorkspacePath(folder, safe);
      setFileDialogVisible(false);
      message.success(`\u5df2\u6253\u5f00\u7a7a\u8bbe\u8ba1\u6587\u4ef6\u5939: ${relativePath(folder, pathDirName(folder))}`);
      return;
    }

  };

  const handleOpenFile = async (path: string, isProject: boolean, notify = true) => {
    try {
      if (isProject) {
        const safe = !browseUnsafe.current;
        const proj = await openProject(path, safe);
        setProject(proj);
        setCurrentFilepath(path);
        setCurrentWorkspacePath(pathDirName(path), safe);
        currentFileSafe.current = safe;
        setFileDialogVisible(false);
        if (notify) message.success(`项目已打开: ${proj.name} (${proj.diagrams.length} 张图)`);
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
        setCurrentWorkspacePath(pathDirName(path), safe);
        currentFileSafe.current = safe;
        setFileDialogVisible(false);
        if (notify) message.success('文件已打开');
      }
    } catch {
      if (useDiagramStore.getState().currentFilepath === path) {
        setCurrentFilepath(null);
      }
      if (notify) message.error('打开文件失败');
    }
  };

  // Restore the last design after a page refresh. The path is persisted by
  // diagramStore; loading the file here restores the actual project content.
  const restoreAttempted = useRef(false);
  useEffect(() => {
    if (restoreAttempted.current || !currentFilepath) return;
    restoreAttempted.current = true;
    browseUnsafe.current = true;
    void handleOpenFile(currentFilepath, /\.umlproj$/i.test(currentFilepath), false);
    // The restore is intentionally performed once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Selecting a project root is a single user action. Resolve its conventional
  // child directories and, when unambiguous, open the only design project.
  async function handleProjectFolderSelect(dirPath: string) {
    const root = normalizePath(dirPath);
    if (!root) return;

    setProjDirBrowseVisible(false);
    browseUnsafe.current = true;
    setProjectRoot(root);
    setCurrentWorkspacePath(root, false);
    currentFileSafe.current = false;
    setCurrentFilepath(null);

    try {
      const rootResult = await browseDirectory(root, false);
      const childDirs = new Map(
        (rootResult.dirs || []).map((item) => [item.name.toLowerCase(), item.path]),
      );
      const designDir = childDirs.get('design') || '';
      const sourcePath = childDirs.get('src') || '';
      const testPath = childDirs.get('test') || '';
      setSourceDir(sourcePath);
      setTestDir(testPath);

      const designResult = designDir
        ? await browseDirectory(designDir, false)
        : null;
      const designFiles = (designResult?.files || []).filter((item) =>
        item.type === 'project'
        || item.name.toLowerCase().endsWith('.umlproj')
        || item.name.toLowerCase().endsWith('.uml'),
      );

      if (designFiles.length === 1) {
        const designFile = designFiles[0];
        await handleOpenFile(designFile.path, designFile.type === 'project' || designFile.name.toLowerCase().endsWith('.umlproj'));
        // Opening the design file sets its own folder as workspace. Keep the
        // project root as the workspace so subsequent saves use project scope.
        setProjectRoot(root);
        setCurrentWorkspacePath(root, false);
        currentFileSafe.current = false;
        message.success(`已加载项目目录，并打开设计文件: ${designFile.name}`);
      } else {
        newProject(pathBaseName(root) || 'Untitled');
        setCurrentFilepath(null);
        setCurrentWorkspacePath(root, false);
        if (designFiles.length > 1) {
          message.info(`项目目录已加载，design 中有 ${designFiles.length} 个设计文件，请手动选择`);
        } else {
          message.success('项目目录已加载，未找到唯一设计文件');
        }
      }
    } catch {
      setSourceDir('');
      setTestDir('');
      newProject(pathBaseName(root) || 'Untitled');
      setCurrentWorkspacePath(root, false);
      message.error('项目目录加载失败，已保留项目根目录');
    }
  }

  // Quick save (always saves as .umlproj project)
  const handleSave = async () => {
    if (!currentFilepath && !currentWorkspacePath) {
      openSaveAs();
      return;
    }
    try {
      // 若工程名仍是默认值，则用当前文件路径的文件名同步工程名
      let proj = project;
      const curName = (currentFilepath || '').replace(/[\\/]+/g, '/').split('/').pop() || '';
      const curBase = curName.replace(/\.umlproj$/i, '').replace(/\.uml$/i, '');
      if ((!proj.name || proj.name === 'Untitled') && curBase) {
        proj = { ...proj, name: curBase };
        setProject(proj);
      }
      const targetPath = currentFilepath ||
        `${normalizePath(currentWorkspacePath!)}/${fileStem(curBase || proj.name || 'Untitled')}.umlproj`;
      const targetSafe = currentFilepath ? currentFileSafe.current : currentWorkspaceSafe;
      const result = await saveProject(proj, targetPath, targetSafe);
      markSaved(result.revision);
      setCurrentFilepath(result.filepath);
      setCurrentWorkspacePath(pathDirName(result.filepath), targetSafe);
      message.success(`项目已保存: ${result.filename}`);
    } catch {
      message.error('保存失败');
    }
  };

  // Open Save As dialog
  const openSaveAs = async () => {
    setSaveFilename(project.name || 'Untitled');
    try {
      const result = await browseDirectory(
        currentWorkspacePath || '',
        currentWorkspacePath ? currentWorkspaceSafe : true,
      );
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
      // Save in the active workspace when one was selected; otherwise retain
      // the historical default uml_dir behavior.
      const filename = fname.toLowerCase().endsWith('.umlproj') ? fname : `${fname}.umlproj`;
      const targetPath = currentWorkspacePath
        ? `${normalizePath(currentWorkspacePath)}/${filename}`
        : filename;
      const result = await saveProject(
        { ...project, name: projName },
        targetPath,
        currentWorkspacePath ? currentWorkspaceSafe : true,
      );
      markSaved(result.revision);
      setCurrentFilepath(result.filepath);
      setCurrentWorkspacePath(pathDirName(result.filepath), currentWorkspacePath ? currentWorkspaceSafe : true);
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

  // ── View controls ───────────────────────────────────
  const handleZoomIn = () => useDiagramStore.getState().setZoom(viewport.zoom * 1.2);
  const handleZoomOut = () => useDiagramStore.getState().setZoom(viewport.zoom / 1.2);
  const handleZoomReset = () => useDiagramStore.getState().setZoom(1.0);

  const saveMenuItems = [
    { key: 'save', label: copy('save') + (isModified ? ' ●' : ''), onClick: handleSave },
    { key: 'saveas', label: copy('saveAs'), onClick: openSaveAs },
  ];

  return (
    <div className="toolbar">
      {/* Row 1: File + Diagrams + Undo/Redo + LLM */}
      <div className="toolbar-row">
      <div className="toolbar-left">
        {/* File Ops */}
        <Tooltip title={copy('newProject')}>
          <Button icon={<FileAddOutlined />} onClick={handleNew} />
        </Tooltip>
        <Tooltip title={interfaceLanguage === 'en'
          ? 'Load a project folder and sync its design/src/test subdirectories.'
          : '\u52a0\u8f7d\u9879\u76ee\u6839\u76ee\u5f55\uff0c\u5e76\u81ea\u52a8\u540c\u6b65 design/src/test \u5b50\u76ee\u5f55\u3002'}>
          <Tag
            icon={<FolderOpenOutlined />}
            color={projectRoot ? 'purple' : 'default'}
            style={{ cursor: 'pointer', margin: 0, fontSize: 12, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 4 }}
            onClick={() => handleBrowseDirFor('project')}
          >
            {interfaceLanguage === 'en' ? 'Project folder' : '\u9879\u76ee\u76ee\u5f55'}{projectRoot ? ': ' + projectRoot.split(/[/\\]/).slice(-2).join('/') : ''}
          </Tag>
        </Tooltip>
        {projectRoot && (
          <Tooltip title={interfaceLanguage === 'en' ? 'Clear project folder' : '\u6e05\u9664\u9879\u76ee\u76ee\u5f55'}>
            <Button size="small" type="text" icon={<CloseOutlined />} onClick={() => {
              setProjectRoot('');
              setSourceDir('');
              setTestDir('');
              setCurrentFilepath(null);
              setCurrentWorkspacePath(null, true);
            }} style={{ padding: 0, minWidth: 18, height: 18 }} />
          </Tooltip>
        )}

        <Dropdown menu={{ items: saveMenuItems }} trigger={['click']}>
          <Button icon={<SaveOutlined />}>
            {isModified ? '● ' : ''}{copy('save')} <DownOutlined />
          </Button>
        </Dropdown>
        <Divider type="vertical" />

        {/* Diagram dropdowns — grouped by type */}
        {(() => {
          const TYPE_SPECS = [
            { key: 'component', label: copy('componentDiagram'), icon: <BlockOutlined />, color: '#d48806' },
            { key: 'class', label: copy('classDiagram'), icon: <ApartmentOutlined />, color: '#1677ff' },
            { key: 'sequence', label: copy('sequenceDiagram'), icon: <ClockCircleOutlined />, color: '#52c41a' },
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

        <Tooltip title={copy('addDiagram')}>
          <Dropdown menu={{
            items: [
              { key: 'class', label: copy('classDiagram'), icon: <ApartmentOutlined />,
                onClick: () => addDiagram('class') },
              { key: 'sequence', label: copy('sequenceDiagram'), icon: <ClockCircleOutlined />,
                onClick: () => addDiagram('sequence') },
              { key: 'component', label: copy('componentDiagram'), icon: <BlockOutlined />,
                onClick: () => addDiagram('component') },
            ],
          }} trigger={['click']}>
            <Button icon={<PlusSquareOutlined />} />
          </Dropdown>
        </Tooltip>

        <Divider type="vertical" />

        {/* Undo/Redo */}
        <Tooltip title={copy('undo') + ' Ctrl+Z'}>
          <Button icon={<UndoOutlined />} disabled={undoStack.length === 0} onClick={undo} />
        </Tooltip>
        <Tooltip title={copy('redo') + ' Ctrl+Y'}>
          <Button icon={<RedoOutlined />} disabled={redoStack.length === 0} onClick={redo} />
        </Tooltip>
      </div>
      <div className="toolbar-right"><SettingsPopover /></div>
      </div>

      {/* Row 2: Design, source and test directories */}
      <div className="toolbar-row" style={{ padding: '2px 0' }}>
        <div className="toolbar-left" style={{ gap: 10, flexWrap: 'wrap', width: '100%' }}>
          <Tooltip title={interfaceLanguage === 'en'
            ? 'Choose a design project from the project design folder.'
            : '\u9009\u62e9 design \u76ee\u5f55\u4e2d\u7684\u8bbe\u8ba1\u6587\u4ef6\u3002'}>
            <Tag
              icon={<ProjectOutlined />}
              color={currentFilepath ? 'purple' : 'default'}
              style={{ cursor: 'pointer', margin: 0, fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}
              onClick={handleBrowseDesign}
            >
              {interfaceLanguage === 'en' ? 'Design' : '\u8bbe\u8ba1'}{currentFilepath ? ': ' + pathBaseName(currentFilepath) : ''}
            </Tag>
          </Tooltip>
          {currentFilepath && (
            <Tooltip title={interfaceLanguage === 'en' ? 'Clear design file' : '\u6e05\u9664\u8bbe\u8ba1\u6587\u4ef6'}>
              <Button size="small" type="text" icon={<CloseOutlined />} onClick={() => setCurrentFilepath(null)} style={{ padding: 0, minWidth: 18, height: 18 }} />
            </Tooltip>
          )}
          <Tooltip title={interfaceLanguage === 'en'
            ? 'Select a source directory for incremental AI-assisted development.'
            : '选择源码目录，用于基于已有代码进行增量开发。'}>
            <Tag
              icon={<FolderOpenOutlined />}
              color={sourceDir ? 'blue' : 'default'}
              style={{ cursor: 'pointer', margin: 0, fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}
              onClick={() => handleBrowseDirFor('source')}
            >
              {copy('sourceDirectory')}{sourceDir ? ': ' + sourceDir.split(/[/\\]/).slice(-2).join('/') : ''}
            </Tag>
          </Tooltip>
          {sourceDir && (
            <Tooltip title={interfaceLanguage === 'en' ? 'Clear source directory' : '清除源码目录'}>
              <Button size="small" type="text" icon={<CloseOutlined />} onClick={() => setSourceDir('')} style={{ padding: 0, minWidth: 18, height: 18 }} />
            </Tooltip>
          )}
          <Tooltip title={interfaceLanguage === 'en'
            ? 'Select an existing test directory for verification.'
            : '选择测试目录，用于运行已有测试。'}>
            <Tag
              icon={<FolderOpenOutlined />}
              color={testDir ? 'green' : 'default'}
              style={{ cursor: 'pointer', margin: 0, fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}
              onClick={() => handleBrowseDirFor('test')}
            >
              {copy('testDirectory')}{testDir ? ': ' + testDir.split(/[/\\]/).slice(-2).join('/') : ''}
            </Tag>
          </Tooltip>
          {testDir && (
            <Tooltip title={interfaceLanguage === 'en' ? 'Clear test directory' : '清除测试目录'}>
              <Button size="small" type="text" icon={<CloseOutlined />} onClick={() => setTestDir('')} style={{ padding: 0, minWidth: 18, height: 18 }} />
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
        <Tooltip title={copy('globalOptimize')}>
          <Button icon={<RobotOutlined />} onClick={() => setGlobalOptimizeVisible(true)} style={{ color: '#722ed1' }}>
            {copy('globalOptimize')}
          </Button>
        </Tooltip>
        <Tooltip title={copy('assistant')}>
          <Button
            icon={<MessageOutlined />}
            onClick={() => setAgentChatVisible(true)}
            type={agentChatVisible ? 'primary' : 'default'}
            style={agentChatVisible ? { color: '#fff', borderColor: '#1677ff', background: '#1677ff' } : {}}
          >
            {copy('assistant')}
          </Button>
        </Tooltip>
        <Tooltip title="Trace 回放（查看会话 LLM / 工具调用记录）">
          <Button icon={<HistoryOutlined />} onClick={() => setTraceVisible(true)}>
            Trace
          </Button>
        </Tooltip>

        <Divider type="vertical" />

        <Tooltip title={copy('evaluation')}>
          <Button icon={<LineChartOutlined />} onClick={() => setEvaluationVisible(true)}>
            {copy('evaluation')}
          </Button>
        </Tooltip>

        {/* Export */}
        <Tooltip title={copy('exportMarkdown')}>
          <Button icon={<FileMarkdownOutlined />} onClick={handleExportMd}>
            {copy('exportMarkdown')}
          </Button>
        </Tooltip>
      </div>

      <div className="toolbar-right">
        <Tooltip title={copy('grid')}>
          <Button
            icon={diagram.grid_visible ? <AppstoreOutlined /> : <EyeInvisibleOutlined />}
            onClick={toggleGrid}
          />
        </Tooltip>

        <Tooltip title={copy('gridSettings')}>
          <Button
            icon={<SettingOutlined />}
            onClick={() => setGridSettingsVisible(true)}
          >
            {diagram.grid_size}px
          </Button>
        </Tooltip>

        <Divider type="vertical" />

        <Tooltip title={showTestCaseInCanvas ? (interfaceLanguage === 'en' ? 'Return to UML canvas' : '返回 UML 画布') : copy('testCases')}>
          <Button
            icon={<TableOutlined />}
            type={showTestCaseInCanvas ? 'primary' : 'default'}
            onClick={toggleTestCaseInCanvas}
          >
            {copy('testCases')}
          </Button>
        </Tooltip>

        <Tooltip title={copy('zoomOut')}>
          <Button icon={<ZoomOutOutlined />} onClick={handleZoomOut} />
        </Tooltip>
            <span className="zoom-label">{Math.round(viewport.zoom * 100)}%</span>
        <Tooltip title={copy('zoomIn')}>
          <Button icon={<ZoomInOutlined />} onClick={handleZoomIn} />
        </Tooltip>
        <Tooltip title={copy('resetZoom')}>
          <Button icon={<ExpandOutlined />} onClick={handleZoomReset} />
        </Tooltip>
      </div>
      </div>

      {/* ── File Open Dialog with folder browsing ────── */}
      <Modal
        title={interfaceLanguage === 'en' ? 'Open UML file' : '打开 UML 文件'}
        open={fileDialogVisible}
        onCancel={() => { setFileDialogVisible(false); browseUnsafe.current = false; }}
        footer={null}
        width={650}
      >
        {/* Breadcrumb / navigation */}
        <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Button size="small" onClick={handleBrowseParent}
            disabled={!browseData?.parent}>
            {interfaceLanguage === 'en' ? 'Parent directory' : '上级目录'}
          </Button>
          <span style={{ fontSize: 12, color: '#666', wordBreak: 'break-all', flex: 1 }}>
            {browseData?.current || ''}
          </span>
        </div>
        <div style={{ marginBottom: 10, display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            size="small"
            type="primary"
            icon={<FolderOpenOutlined />}
            onClick={handleSelectCurrentFolder}
          >
            {interfaceLanguage === 'en' ? 'Use this folder as workspace' : '在此文件夹中工作'}
          </Button>
        </div>

        {/* Path input + Go button */}
        <div style={{ marginBottom: 8, display: 'flex', gap: 8 }}>
          <Input
            size="small"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            onPressEnter={() => handleNavigateTo(pathInput)}
            placeholder={interfaceLanguage === 'en' ? 'Enter or paste a directory path, then press Enter...' : '输入或粘贴目录路径，按回车跳转...'}
            style={{ flex: 1 }}
            allowClear
          />
          <Button
            size="small"
            type="primary"
            onClick={() => handleNavigateTo(pathInput)}
          >
            {interfaceLanguage === 'en' ? 'Go' : '跳转'}
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
              {localizedQuickPath(qp.label)}
            </Button>
          ))}
        </div>

        <List
          loading={false}
          locale={{ emptyText: interfaceLanguage === 'en' ? 'No saved files' : '暂无保存的文件' }}
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

      {/* ── Grid Settings Modal ──────────────────────── */}
      <Modal
        title={copy('gridSettings')}
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
        title={`选择${projDirBrowseTarget === 'project' ? '项目' : projDirBrowseTarget === 'source' ? '源码' : '测试'}目录`}
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
              onClick={() => handleProjDirNav(dir.path)}
              style={{ cursor: 'pointer' }}
              actions={[
                <Button
                  key="select"
                  size="small"
                  type="link"
                  onClick={(e) => { e.stopPropagation(); handleProjDirSelect(dir.path); }}
                >
                  选择
                </Button>,
              ]}
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

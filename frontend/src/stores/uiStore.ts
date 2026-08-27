/** UI store – manages UI panel state and global UI preferences. */

import { create } from 'zustand';
import type { UmlDiagram } from '../types/uml';

export type RightPanelTab = 'properties' | 'diff' | 'testcase';
export type Language = 'python' | 'java' | 'typescript' | 'javascript' | 'csharp' | 'cpp' |
  'go' | 'rust' | 'ruby' | 'swift' | 'kotlin' | 'php';
export type DiffDiagramType = string;  // was: 'class' | 'sequence' | 'component' — now dynamic

interface UiState {
  // Panel visibility
  rightPanelVisible: boolean;
  rightPanelTab: RightPanelTab;
  rightPanelWidth: number;

  // Language
  selectedLanguage: Language;

  // Test code viewer (test case code)
  generatedTestCode: Record<string, string> | null;
  activeTestFile: string | null;

  // Diff / optimization
  diffContent: string | null;
  originalCode: Record<string, string> | null;
  optimizedCode: Record<string, string> | null;
  originalDiagram: UmlDiagram | null;
  optimizedDiagram: UmlDiagram | null;
  showingOptimized: boolean;
  optimizeInstructions: string; // last optimization request content

  // Global multi-diagram optimization (pipeline Stage 1)
  originalDiagrams: Record<string, any>;     // { class: {...}, sequence: {...}, component: {...} }
  optimizedDiagrams: Record<string, any>;
  diffContents: Record<string, string>;       // per-type diff text
  activeDiffDiagramType: DiffDiagramType;     // which diagram tab is active in DiffViewer
  optimizationConsistencyReport: any[];        // cross-validation consistency report

  showTestCaseInCanvas: boolean; // toggle main canvas to show test cases
  testCaseData: string; // Excel test case summary for pipeline Stage 5

  // Project directories (shared by Agent chat, etc.)
  sourceDir: string;
  testDir: string;

  // Modal
  fileDialogVisible: boolean;
  exportDialogVisible: boolean;
  currentBrowsePath: string;

  // Agent Chat dialog
  agentChatVisible: boolean;
  agentChatExpanded: boolean;
  agentChatPosition: { x: number; y: number }; // 面板左上角 (left/top, px)

  // Trace viewer
  traceVisible: boolean;

  // Actions
  toggleRightPanel: () => void;
  setRightPanelVisible: (visible: boolean) => void;
  setRightPanelTab: (tab: RightPanelTab) => void;
  setRightPanelWidth: (width: number) => void;

  setSelectedLanguage: (lang: Language) => void;

  setGeneratedTestCode: (code: Record<string, string> | null) => void;
  setActiveTestFile: (file: string | null) => void;
  setDiffContent: (diff: string | null) => void;
  setOriginalCode: (code: Record<string, string> | null) => void;
  setOptimizedCode: (code: Record<string, string> | null) => void;
  setOptimizationResult: (original: UmlDiagram, optimized: UmlDiagram, diff: string, instructions: string) => void;
  setGlobalOptimizationResult: (
    originals: Record<string, any>,
    optimizeds: Record<string, any>,
    diffs: Record<string, string>,
    report: any[],
    instructions: string,
  ) => void;
  setActiveDiffDiagramType: (type: DiffDiagramType) => void;
  toggleShowingVersion: () => void;
  setShowingOptimized: (v: boolean) => void;
  toggleTestCaseInCanvas: () => void;
  setShowTestCaseInCanvas: (v: boolean) => void;
  setTestCaseData: (data: string) => void;

  setSourceDir: (dir: string) => void;
  setTestDir: (dir: string) => void;

  setFileDialogVisible: (visible: boolean) => void;
  setExportDialogVisible: (visible: boolean) => void;
  setCurrentBrowsePath: (path: string) => void;

  // Agent Chat
  setAgentChatVisible: (visible: boolean) => void;
  setAgentChatExpanded: (expanded: boolean) => void;
  setAgentChatPosition: (position: { x: number; y: number }) => void;

  // Trace viewer
  setTraceVisible: (visible: boolean) => void;
}

export const useUiStore = create<UiState>((set, get) => ({
  rightPanelVisible: true,
  rightPanelTab: 'properties',
  rightPanelWidth: 420,
  selectedLanguage: 'python',
  generatedTestCode: null,
  activeTestFile: null,
  diffContent: null,
  originalCode: null,
  optimizedCode: null,
  originalDiagram: null,
  optimizedDiagram: null,
  showingOptimized: false,
  optimizeInstructions: '',
  originalDiagrams: {},
  optimizedDiagrams: {},
  diffContents: {},
  activeDiffDiagramType: 'class',
  optimizationConsistencyReport: [],
  showTestCaseInCanvas: false,
  testCaseData: '',
  sourceDir: localStorage.getItem('sourceDir') || '',
  testDir: localStorage.getItem('testDir') || '',
  fileDialogVisible: false,
  exportDialogVisible: false,
  currentBrowsePath: '',
  agentChatVisible: false,
  agentChatExpanded: false,
  traceVisible: false,
  agentChatPosition: (() => {
    try {
      const saved = localStorage.getItem('agentChatPosition');
      if (saved) {
        const p = JSON.parse(saved);
        if (typeof p?.x === 'number' && typeof p?.y === 'number') {
          // 若可视区变化导致位置出界，收敛回可视区
          return {
            x: Math.max(0, Math.min(p.x, window.innerWidth - 120)),
            y: Math.max(0, Math.min(p.y, window.innerHeight - 60)),
          };
        }
      }
    } catch { /* ignore */ }
    return { x: window.innerWidth - 420 - 24, y: window.innerHeight - 520 - 24 };
  })(),

  toggleRightPanel: () => set((s) => ({ rightPanelVisible: !s.rightPanelVisible })),
  setRightPanelVisible: (visible: boolean) => set({ rightPanelVisible: visible }),
  setRightPanelTab: (tab) => set((s) => ({
    rightPanelTab: tab,
    rightPanelVisible: s.rightPanelVisible && s.rightPanelTab !== tab ? true : s.rightPanelVisible,
  })),
  setRightPanelWidth: (width) => set({ rightPanelWidth: width }),

  setSelectedLanguage: (lang) => set({ selectedLanguage: lang }),

  setGeneratedTestCode: (code) => set({ generatedTestCode: code, activeTestFile: code ? Object.keys(code)[0] || null : null }),
  setActiveTestFile: (file) => set({ activeTestFile: file }),
  setDiffContent: (diff) => set({ diffContent: diff }),
  setOriginalCode: (code) => set({ originalCode: code }),
  setOptimizedCode: (code) => set({ optimizedCode: code }),

  setOptimizationResult: (original, optimized, diff, instructions) => set({
    originalDiagram: original,
    optimizedDiagram: optimized,
    originalCode: { 'original.uml': JSON.stringify(original, null, 2) },
    optimizedCode: { 'optimized.uml': JSON.stringify(optimized, null, 2) },
    diffContent: diff,
    optimizeInstructions: instructions || '',
    showingOptimized: false,
  }),

  setGlobalOptimizationResult: (originals, optimizeds, diffs, report, instructions) => {
    const firstType = (Object.keys(optimizeds)[0] || 'class') as DiffDiagramType;
    const firstOrig = originals[firstType];
    const firstOpt = optimizeds[firstType];
    const firstDiff = diffs[firstType] || '';
    // 空工程/new design：原始版和优化版相同 → 默认显示优化版
    const sameContents = firstOrig && firstOpt
      && JSON.stringify(firstOrig) === JSON.stringify(firstOpt);
    set({
      originalDiagrams: originals,
      optimizedDiagrams: optimizeds,
      diffContents: diffs,
      activeDiffDiagramType: firstType,
      optimizationConsistencyReport: report || [],
      // Also populate single-diagram fields for backward compat
      originalDiagram: firstOrig || null,
      optimizedDiagram: firstOpt || null,
      originalCode: firstOrig ? { [`original_${firstType}.json`]: JSON.stringify(firstOrig, null, 2) } : null,
      optimizedCode: firstOpt ? { [`optimized_${firstType}.json`]: JSON.stringify(firstOpt, null, 2) } : null,
      diffContent: firstDiff,
      optimizeInstructions: instructions || '',
      showingOptimized: sameContents,
    });
  },

  setActiveDiffDiagramType: (type) => {
    const state = get();
    const orig = state.originalDiagrams[type];
    const opt = state.optimizedDiagrams[type];
    const diff = state.diffContents[type] || '';
    set({
      activeDiffDiagramType: type,
      originalDiagram: orig || null,
      optimizedDiagram: opt || null,
      originalCode: orig ? { [`original_${type}.json`]: JSON.stringify(orig, null, 2) } : null,
      optimizedCode: opt ? { [`optimized_${type}.json`]: JSON.stringify(opt, null, 2) } : null,
      diffContent: diff,
      showingOptimized: false,
    });
  },

  toggleShowingVersion: () => set((s) => ({ showingOptimized: !s.showingOptimized })),
  setShowingOptimized: (v) => set({ showingOptimized: v }),
  toggleTestCaseInCanvas: () => set((s) => ({ showTestCaseInCanvas: !s.showTestCaseInCanvas })),
  setShowTestCaseInCanvas: (v) => set({ showTestCaseInCanvas: v }),
  setTestCaseData: (data) => set({ testCaseData: data }),

  setSourceDir: (dir) => {
    localStorage.setItem('sourceDir', dir);
    set({ sourceDir: dir });
  },
  setTestDir: (dir) => {
    localStorage.setItem('testDir', dir);
    set({ testDir: dir });
  },

  setFileDialogVisible: (visible) => set({ fileDialogVisible: visible }),
  setExportDialogVisible: (visible) => set({ exportDialogVisible: visible }),
  setCurrentBrowsePath: (path) => set({ currentBrowsePath: path }),

  setAgentChatVisible: (visible) => set({ agentChatVisible: visible }),
  setAgentChatExpanded: (expanded) => set({ agentChatExpanded: expanded }),
  setAgentChatPosition: (position) => {
    try {
      localStorage.setItem('agentChatPosition', JSON.stringify(position));
    } catch { /* ignore */ }
    set({ agentChatPosition: position });
  },

  setTraceVisible: (visible) => set({ traceVisible: visible }),
}));

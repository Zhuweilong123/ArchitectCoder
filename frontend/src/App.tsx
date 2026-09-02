/**
 * Main application shell: toolbar, canvas, and the contextual side panel.
 */

import React, { useCallback } from 'react';
import { Layout, Tabs, Button, Tooltip } from 'antd';
import {
  SettingOutlined,
  DiffOutlined, CloseOutlined, FileTextOutlined,
} from '@ant-design/icons';
import UMLEditor from './components/Canvas/UMLEditor';
import SeqEditor from './components/Canvas/SeqEditor';
import CompEditor from './components/Canvas/CompEditor';
import Toolbar from './components/Toolbar/Toolbar';
import PropertyPanel from './components/PropertyPanel/PropertyPanel';
import DiffViewer from './components/DiffViewer/DiffViewer';
import TestCaseViewer from './components/TestCaseViewer/TestCaseViewer';
import TestCodeViewer from './components/TestCodeViewer/TestCodeViewer';
import AgentChat from './components/AgentChat/AgentChat';
import TraceViewer from './components/TraceViewer/TraceViewer';
import EvaluationCenter from './components/EvaluationCenter/EvaluationCenter';
import { useUiStore, type RightPanelTab } from './stores/uiStore';
import { useDiagramStore } from './stores/diagramStore';
import { t, type TranslationKey } from './i18n';
import './App.css';

const { Content } = Layout;

const App: React.FC = () => {
  const {
    rightPanelVisible, rightPanelTab, rightPanelWidth,
    setRightPanelTab, setRightPanelWidth, toggleRightPanel,
    showTestCaseInCanvas, interfaceLanguage,
  } = useUiStore();
  const diagramType = useDiagramStore((s) => s.diagram.diagram_type || 'class');
  const activeIdx = useDiagramStore((s) => s.project.active_diagram_index);
  const hasDiagrams = useDiagramStore((s) => s.project.diagrams.length > 0);
  const copy = (key: TranslationKey) => t(interfaceLanguage, key);

  const handleResize = useCallback((_e: React.MouseEvent, direction: string) => {
    if (direction === 'left') {
      const handleMove = (moveEvent: MouseEvent) => {
        const newWidth = window.innerWidth - moveEvent.clientX;
        setRightPanelWidth(Math.max(280, Math.min(800, newWidth)));
      };
      const handleUp = () => {
        document.removeEventListener('mousemove', handleMove);
        document.removeEventListener('mouseup', handleUp);
      };
      document.addEventListener('mousemove', handleMove);
      document.addEventListener('mouseup', handleUp);
    }
  }, [setRightPanelWidth]);

  const tabItems = [
    {
      key: 'properties' as RightPanelTab,
      label: (
        <Tooltip title={copy('properties')}>
          <SettingOutlined />
        </Tooltip>
      ),
      children: <PropertyPanel />,
    },
    {
      key: 'diff' as RightPanelTab,
      label: (
        <Tooltip title={copy('diff')}>
          <DiffOutlined />
        </Tooltip>
      ),
      children: <DiffViewer />,
    },
    {
      key: 'testcase' as RightPanelTab,
      label: (
        <Tooltip title={copy('testcaseCode')}>
          <FileTextOutlined />
        </Tooltip>
      ),
      children: <TestCodeViewer />,
    },
  ];

  const statusText = showTestCaseInCanvas
    ? (interfaceLanguage === 'en'
      ? 'Double-click a cell to edit test cases · Supports full and incremental test generation'
      : '双击单元格编辑用例 · 支持全量和增量生成测试代码')
    : !hasDiagrams
      ? copy('noDiagramHint')
      : diagramType === 'sequence'
        ? (interfaceLanguage === 'en'
          ? 'Add elements from the toolbar · Click lifeline A then lifeline B to create a message · Ctrl + wheel to zoom'
          : '从工具栏添加元素 · 依次点击生命线 A 和 B 创建消息 · Ctrl + 滚轮缩放')
        : diagramType === 'component'
          ? (interfaceLanguage === 'en'
            ? 'Add components from the toolbar · Drag ports to create dependencies · Ctrl + wheel to zoom · Space + drag to pan'
            : '从工具栏添加组件 · 拖动端口创建依赖 · Ctrl + 滚轮缩放 · 按住空格拖动画布')
          : (interfaceLanguage === 'en'
            ? 'Add classes from the toolbar · Drag ports to create relationships · Ctrl + wheel to zoom · Space + drag to pan'
            : '从工具栏添加类 · 拖动端口创建连接 · Ctrl + 滚轮缩放 · 按住空格拖动画布');

  return (
    <Layout className="app-layout">
      <Toolbar />

      <Layout className="app-main">
        <Content className="app-content">
          {showTestCaseInCanvas ? (
            <TestCaseViewer embedded />
          ) : !hasDiagrams ? (
            <div className="empty-canvas">
              <div className="empty-canvas-icon">⌘</div>
              <p className="empty-canvas-title">{copy('noDiagram')}</p>
              <p className="empty-canvas-hint">{copy('noDiagramHint')}</p>
            </div>
          ) : diagramType === 'sequence' ? (
            <SeqEditor key={'seq_' + activeIdx} />
          ) : diagramType === 'component' ? (
            <CompEditor key={'comp_' + activeIdx} />
          ) : (
            <UMLEditor key={'uml_' + activeIdx} />
          )}

          <div className="status-bar">
            <span>{statusText}</span>
            <span>{interfaceLanguage === 'en' ? 'Ctrl + Z Undo · Ctrl + Y Redo' : 'Ctrl + Z 撤销 · Ctrl + Y 重做'}</span>
          </div>
        </Content>

        {rightPanelVisible && (
          <div
            className="resize-handle"
            onMouseDown={(e) => handleResize(e, 'left')}
          />
        )}

        {rightPanelVisible && (
          <div className="right-panel" style={{ width: rightPanelWidth }}>
            <div className="right-panel-tabs">
              <Tabs
                activeKey={rightPanelTab}
                onChange={(key) => setRightPanelTab(key as RightPanelTab)}
                size="small"
                tabBarExtraContent={
                  <Button
                    type="text"
                    size="small"
                    icon={<CloseOutlined />}
                    onClick={toggleRightPanel}
                    aria-label={interfaceLanguage === 'en' ? 'Close panel' : '关闭面板'}
                  />
                }
                items={tabItems}
              />
            </div>
          </div>
        )}

        {!rightPanelVisible && (
          <Tooltip title={interfaceLanguage === 'en' ? 'Show side panel' : '显示右侧面板'}>
            <Button
              type="primary"
              shape="circle"
              size="small"
              icon={<SettingOutlined />}
              onClick={toggleRightPanel}
              aria-label={interfaceLanguage === 'en' ? 'Show side panel' : '显示右侧面板'}
              style={{
                position: 'absolute', right: 8, top: 50,
                zIndex: 100, boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
              }}
            />
          </Tooltip>
        )}
      </Layout>

      <AgentChat />
      <TraceViewer />
      <EvaluationCenter />
    </Layout>
  );
};

export default App;
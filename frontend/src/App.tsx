/**
 * Main App Component – layout with toolbar, canvas, and right panel.
 */

import React, { useCallback } from 'react';
import { Layout, Tabs, Button, Tooltip } from 'antd';
import {
  SettingOutlined, CodeOutlined, PlayCircleOutlined,
  DiffOutlined, CloseOutlined, FileTextOutlined,
} from '@ant-design/icons';
import UMLEditor from './components/Canvas/UMLEditor';
import SeqEditor from './components/Canvas/SeqEditor';
import CompEditor from './components/Canvas/CompEditor';
import Toolbar from './components/Toolbar/Toolbar';
import PropertyPanel from './components/PropertyPanel/PropertyPanel';
import CodeViewer from './components/CodeViewer/CodeViewer';
import PipelineConsole from './components/PipelineConsole/PipelineConsole';
import DiffViewer from './components/DiffViewer/DiffViewer';
import TestCaseViewer from './components/TestCaseViewer/TestCaseViewer';
import TestCodeViewer from './components/TestCodeViewer/TestCodeViewer';
import { useUiStore, type RightPanelTab } from './stores/uiStore';
import { useDiagramStore } from './stores/diagramStore';
import './App.css';

const { Sider, Content } = Layout;

const App: React.FC = () => {
  const {
    rightPanelVisible, rightPanelTab, rightPanelWidth,
    setRightPanelTab, setRightPanelWidth, toggleRightPanel,
    codeGenLoading, showTestCaseInCanvas,
  } = useUiStore();
  const diagramType = useDiagramStore((s) => s.diagram.diagram_type || 'class');
  const activeIdx = useDiagramStore((s) => s.project.active_diagram_index);
  const hasDiagrams = useDiagramStore((s) => s.project.diagrams.length > 0);

  const handleResize = useCallback((_e: React.MouseEvent, direction: string, ref: HTMLElement) => {
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
        <Tooltip title="属性">
          <SettingOutlined />
        </Tooltip>
      ),
      children: <PropertyPanel />,
    },
    {
      key: 'code' as RightPanelTab,
      label: (
        <Tooltip title="代码">
          <CodeOutlined />
        </Tooltip>
      ),
      children: <CodeViewer />,
    },
    {
      key: 'pipeline' as RightPanelTab,
      label: (
        <Tooltip title="流水线">
          <PlayCircleOutlined />
        </Tooltip>
      ),
      children: <PipelineConsole />,
    },
    {
      key: 'diff' as RightPanelTab,
      label: (
        <Tooltip title="对比">
          <DiffOutlined />
        </Tooltip>
      ),
      children: <DiffViewer />,
    },
    {
      key: 'testcase' as RightPanelTab,
      label: (
        <Tooltip title="用例代码">
          <FileTextOutlined />
        </Tooltip>
      ),
      children: <TestCodeViewer />,
    },
  ];

  return (
    <Layout className="app-layout">
      {/* Toolbar */}
      <Toolbar />

      {/* Main Area */}
      <Layout className="app-main">
        <Content className="app-content">
          {showTestCaseInCanvas ? (
            <TestCaseViewer embedded />
          ) : !hasDiagrams ? (
            <div style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              justifyContent: 'center', height: '100%', color: '#999',
            }}>
              <p style={{ fontSize: 18, marginBottom: 8 }}>📋 暂无设计图</p>
              <p style={{ fontSize: 13 }}>
                点击工具栏「全局优化」按钮，输入需求描述生成 UML 设计，
                或使用工具栏「+」下拉菜单手动添加图。
              </p>
            </div>
          ) : diagramType === 'sequence' ? (
            <SeqEditor key={`seq_${activeIdx}`} />
          ) : diagramType === 'component' ? (
            <CompEditor key={`comp_${activeIdx}`} />
          ) : (
            <UMLEditor key={`uml_${activeIdx}`} />
          )}
          {/* Status bar */}
          <div className="status-bar">
            {showTestCaseInCanvas ? (
              <span>双击单元格编辑用例 | 支持增删查改 | 全量/增量生成测试代码</span>
            ) : !hasDiagrams ? (
              <span>点击工具栏「全局优化」输入需求生成 UML 设计</span>
            ) : diagramType === 'sequence' ? (
              <span>工具栏添加元素 | 点击生命线A→再点生命线B创建消息 | Ctrl+滚轮缩放</span>
            ) : diagramType === 'component' ? (
              <span>工具栏添加组件 | 拖拽端口创建依赖 | Ctrl+滚轮缩放 | 空格平移</span>
            ) : (
              <span>工具栏添加类 | 拖拽端口创建连接 | Ctrl+滚轮缩放 | 空格平移</span>
            )}
            <span>Ctrl+Z 撤销 | Ctrl+Y 重做</span>
          </div>
        </Content>

        {/* Resize Handle */}
        {rightPanelVisible && (
          <div
            className="resize-handle"
            onMouseDown={(e) => handleResize(e, 'left', e.currentTarget)}
          />
        )}

        {/* Right Panel */}
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
                  />
                }
                items={tabItems}
              />
            </div>
          </div>
        )}

        {/* Floating button to re-open panel when hidden */}
        {!rightPanelVisible && (
          <Tooltip title="显示右侧面板">
            <Button
              type="primary"
              shape="circle"
              size="small"
              icon={<SettingOutlined />}
              onClick={toggleRightPanel}
              style={{
                position: 'absolute', right: 8, top: 50,
                zIndex: 100, boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
              }}
            />
          </Tooltip>
        )}
      </Layout>
    </Layout>
  );
};

export default App;

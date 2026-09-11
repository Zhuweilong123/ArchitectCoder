import React from 'react';
import {
  Button, Dropdown, Divider, Modal, Tooltip,
} from 'antd';
import {
  ApartmentOutlined, BlockOutlined, ClockCircleOutlined,
  DownOutlined, PlusSquareOutlined, RedoOutlined, UndoOutlined,
} from '@ant-design/icons';
import type { Project } from '../../types/uml';
import type { TranslationKey } from '../../i18n';

interface DiagramTypeControlsProps {
  project: Project;
  copy: (key: TranslationKey) => string;
  setActiveDiagram: (index: number) => void;
  addDiagram: (type: 'class' | 'sequence' | 'component') => void;
  removeDiagram: (index: number) => void;
  undoStack: unknown[];
  redoStack: unknown[];
  undo: () => void;
  redo: () => void;
}

const DiagramTypeControls: React.FC<DiagramTypeControlsProps> = ({
  project,
  copy,
  setActiveDiagram,
  addDiagram,
  removeDiagram,
  undoStack,
  redoStack,
  undo,
  redo,
}) => {
  const typeSpecs = [
    { key: 'component', label: copy('componentDiagram'), icon: <BlockOutlined />, color: '#d48806' },
    { key: 'class', label: copy('classDiagram'), icon: <ApartmentOutlined />, color: '#1677ff' },
    { key: 'sequence', label: copy('sequenceDiagram'), icon: <ClockCircleOutlined />, color: '#52c41a' },
  ] as const;
  const componentDiagram = project.diagrams.find((diagram) => diagram.diagram_type === 'component');
  const activeIndex = project.active_diagram_index;

  const handleDelete = (index: number, name: string) => {
    Modal.confirm({
      title: `删除「${name}」`,
      content: '确认删除此图？此操作不可撤销。',
      okText: '删除', okType: 'danger', cancelText: '取消',
      onOk: () => removeDiagram(index),
    });
  };

  return (
    <>
      {typeSpecs.map((spec) => {
        const items = project.diagrams
          .map((diagram, index) => ({ diagram, index }))
          .filter(({ diagram }) => (diagram.diagram_type || 'class') === spec.key);
        if (items.length === 0) return null;

        const activeItem = items.find(({ index }) => index === activeIndex);
        const displayLabel = activeItem
          ? (() => {
              const diagram = activeItem.diagram;
              const isAuto = !diagram.name || diagram.name === 'Untitled'
                || /^(class|sequence|component)_\d+$/.test(diagram.name);
              const parentComponent = diagram.component_id
                ? (componentDiagram?.components || []).find((component) => component.id === diagram.component_id)
                : null;
              const base = isAuto ? spec.label : diagram.name;
              return parentComponent ? `${parentComponent.name} › ${base}` : base;
            })()
          : `${spec.label} (${items.length})`;

        const menuItems = items.map(({ diagram, index }) => {
          const isActive = index === activeIndex;
          const isAuto = !diagram.name || diagram.name === 'Untitled'
            || /^(class|sequence|component)_\d+$/.test(diagram.name);
          const parentComponent = diagram.component_id
            ? (componentDiagram?.components || []).find((component) => component.id === diagram.component_id)
            : null;
          const itemLabel = isAuto ? spec.label : diagram.name;
          const fullLabel = parentComponent ? `${parentComponent.name} › ${itemLabel}` : itemLabel;
          return {
            key: String(index),
            icon: isActive
              ? <span style={{ color: spec.color, fontWeight: 'bold' }}>✔</span>
              : <span style={{ width: 14, display: 'inline-block' }} />,
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
                  onClick={(event) => { event.stopPropagation(); handleDelete(index, fullLabel); }}
                  title="删除此图"
                >🗑</span>
              </span>
            ),
            onClick: () => setActiveDiagram(index),
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
      })}

      <Tooltip title={copy('addDiagram')}>
        <Dropdown menu={{
          items: [
            { key: 'class', label: copy('classDiagram'), icon: <ApartmentOutlined />, onClick: () => addDiagram('class') },
            { key: 'sequence', label: copy('sequenceDiagram'), icon: <ClockCircleOutlined />, onClick: () => addDiagram('sequence') },
            { key: 'component', label: copy('componentDiagram'), icon: <BlockOutlined />, onClick: () => addDiagram('component') },
          ],
        }} trigger={['click']}>
          <Button icon={<PlusSquareOutlined />} />
        </Dropdown>
      </Tooltip>

      <Divider type="vertical" />
      <Tooltip title={copy('undo') + ' Ctrl+Z'}>
        <Button icon={<UndoOutlined />} disabled={undoStack.length === 0} onClick={undo} />
      </Tooltip>
      <Tooltip title={copy('redo') + ' Ctrl+Y'}>
        <Button icon={<RedoOutlined />} disabled={redoStack.length === 0} onClick={redo} />
      </Tooltip>
    </>
  );
};

export default DiagramTypeControls;

/**
 * Property Panel – edit properties of selected class, relation, or lifeline.
 */

import React from 'react';
import {
  Form, Input, Select, Switch, Button, Collapse, Space,
  Popconfirm, Empty, Divider, InputNumber,
} from 'antd';
import {
  DeleteOutlined, PlusOutlined, MinusCircleOutlined,
} from '@ant-design/icons';
import { useDiagramStore } from '../../stores/diagramStore';
import { useShallow } from 'zustand/react/shallow';
import {
  Visibility, Stereotype, RelationType,
  type UmlAttribute, type UmlMethod,
} from '../../types/uml';
import { MESSAGE_TYPE_LABELS } from '../../types/sequence';
import { useDebouncedDraft } from '../../hooks/useDebouncedDraft';
import './PropertyPanel.css';

const { TextArea } = Input;

const PropertyPanel: React.FC = () => {
  const {
    diagram, selectedClassId, selectedRelationId,
    selectedLifelineId, selectedMessageId,
    selectedComponentId,
    updateClass, removeClass, updateRelation, removeRelation,
    updateLifeline, removeLifeline,
    updateMessage, removeMessage,
    updateComponent, removeComponent,
    project, setActiveDiagram, addDiagram,
  } = useDiagramStore(useShallow((s) => ({
    diagram: s.diagram,
    selectedClassId: s.selectedClassId,
    selectedRelationId: s.selectedRelationId,
    selectedLifelineId: s.selectedLifelineId,
    selectedMessageId: s.selectedMessageId,
    selectedComponentId: s.selectedComponentId,
    updateClass: s.updateClass,
    removeClass: s.removeClass,
    updateRelation: s.updateRelation,
    removeRelation: s.removeRelation,
    updateLifeline: s.updateLifeline,
    removeLifeline: s.removeLifeline,
    updateMessage: s.updateMessage,
    removeMessage: s.removeMessage,
    updateComponent: s.updateComponent,
    removeComponent: s.removeComponent,
    project: s.project,
    setActiveDiagram: s.setActiveDiagram,
    addDiagram: s.addDiagram,
  })));

  const { scheduleDraft, flushDraft, draftValue } = useDebouncedDraft();

  const selectedClass = diagram.classes.find((c) => c.id === selectedClassId);
  const selectedRelation = diagram.relations.find((r) => r.id === selectedRelationId);
  const selectedLifeline = (diagram.lifelines || []).find((l) => l.id === selectedLifelineId);
  const selectedMessage = (diagram.messages || []).find((m) => m.id === selectedMessageId);
  const selectedComponent = (diagram.components || []).find((c) => c.id === selectedComponentId);

  // ── Class Property Editor ──────────────────────────
  if (selectedClass) {
    const handleClassChange = (field: string, value: unknown) => {
      updateClass(selectedClass.id, { [field]: value });
    };
    const updateAttributeField = (index: number, field: string, value: unknown) => {
      const liveClass = useDiagramStore.getState().diagram.classes.find((item) => item.id === selectedClass.id);
      if (!liveClass?.attributes[index]) return;
      const attributes = [...liveClass.attributes];
      attributes[index] = { ...attributes[index], [field]: value };
      updateClass(selectedClass.id, { attributes });
    };
    const updateMethodField = (index: number, field: string, value: unknown) => {
      const liveClass = useDiagramStore.getState().diagram.classes.find((item) => item.id === selectedClass.id);
      if (!liveClass?.methods[index]) return;
      const methods = [...liveClass.methods];
      methods[index] = { ...methods[index], [field]: value };
      updateClass(selectedClass.id, { methods });
    };

    return (
      <div className="property-panel">
        <div className="property-panel-header">
          <h3>类属性</h3>
          <Popconfirm
            title="确认删除此类？"
            onConfirm={() => removeClass(selectedClass.id)}
            okText="删除" cancelText="取消"
          >
            <Button danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </div>

        <Form layout="vertical" size="small">
          <Form.Item label="类名">
            <Input
              value={draftValue(`class:${selectedClass.id}:name`, selectedClass.name)}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`class:${selectedClass.id}:name`, value,
                  () => updateClass(selectedClass.id, { name: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`class:${selectedClass.id}:name`);
                if (typeof value === 'string') updateClass(selectedClass.id, { name: value });
              }}
            />
          </Form.Item>
          <Form.Item label="构造型">
            <Select
              value={selectedClass.stereotype}
              onChange={(v) => handleClassChange('stereotype', v)}
              options={Object.values(Stereotype).map((s) => ({ value: s, label: s }))}
            />
          </Form.Item>
          <Form.Item label="备注">
            <TextArea
              value={draftValue(`class:${selectedClass.id}:note`, selectedClass.note)}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`class:${selectedClass.id}:note`, value,
                  () => updateClass(selectedClass.id, { note: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`class:${selectedClass.id}:note`);
                if (typeof value === 'string') updateClass(selectedClass.id, { note: value });
              }}
              rows={2}
              placeholder="添加备注..."
            />
          </Form.Item>
          <Form.Item label="提供的接口 (◉ provided)">
            <TextArea
              value={draftValue(`class:${selectedClass.id}:provided_interfaces`,
                (selectedClass.provided_interfaces || []).join('\n'))}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`class:${selectedClass.id}:provided_interfaces`, value,
                  () => updateClass(selectedClass.id, {
                    provided_interfaces: value.split('\n').filter((s) => s.trim()),
                  }));
              }}
              onBlur={() => {
                const value = flushDraft(`class:${selectedClass.id}:provided_interfaces`);
                if (typeof value === 'string') updateClass(selectedClass.id, {
                  provided_interfaces: value.split('\n').filter((s) => s.trim()),
                });
              }}
              rows={2}
              placeholder="IService&#10;IRepository"
            />
          </Form.Item>
          <Form.Item label="依赖的接口 (◡ required)">
            <TextArea
              value={draftValue(`class:${selectedClass.id}:required_interfaces`,
                (selectedClass.required_interfaces || []).join('\n'))}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`class:${selectedClass.id}:required_interfaces`, value,
                  () => updateClass(selectedClass.id, {
                    required_interfaces: value.split('\n').filter((s) => s.trim()),
                  }));
              }}
              onBlur={() => {
                const value = flushDraft(`class:${selectedClass.id}:required_interfaces`);
                if (typeof value === 'string') updateClass(selectedClass.id, {
                  required_interfaces: value.split('\n').filter((s) => s.trim()),
                });
              }}
              rows={2}
              placeholder="IDatabase&#10;ILogger"
            />
          </Form.Item>
        </Form>

        {/* Attributes */}
        <Collapse
          ghost
          defaultActiveKey={['attrs']}
          items={[{
            key: 'attrs',
            label: `属性 (${selectedClass.attributes.length})`,
            children: (
              <div>
                {selectedClass.attributes.map((attr, idx) => (
                  <div key={idx} className="property-row">
                    <Select
                      value={attr.visibility}
                      size="small"
                      style={{ width: 50 }}
                      onChange={(v) => {
                        const attrs = [...selectedClass.attributes];
                        attrs[idx] = { ...attrs[idx], visibility: v };
                        handleClassChange('attributes', attrs);
                      }}
                      options={Object.values(Visibility).map((v) => ({ value: v, label: v }))}
                    />
                    <Input
                      size="small"
                      style={{ width: 80 }}
                      value={draftValue(`class:${selectedClass.id}:attribute:${idx}:name`, attr.name)}
                      placeholder="名称"
                      onChange={(e) => {
                        const value = e.target.value;
                        scheduleDraft(`class:${selectedClass.id}:attribute:${idx}:name`, value,
                          () => updateAttributeField(idx, 'name', value));
                      }}
                      onBlur={() => {
                        const value = flushDraft(`class:${selectedClass.id}:attribute:${idx}:name`);
                        if (typeof value === 'string') updateAttributeField(idx, 'name', value);
                      }}
                    />
                    <span className="attr-colon">:</span>
                    <Input
                      size="small"
                      style={{ width: 80 }}
                      value={draftValue(`class:${selectedClass.id}:attribute:${idx}:type`, attr.type)}
                      placeholder="类型"
                      onChange={(e) => {
                        const value = e.target.value;
                        scheduleDraft(`class:${selectedClass.id}:attribute:${idx}:type`, value,
                          () => updateAttributeField(idx, 'type', value));
                      }}
                      onBlur={() => {
                        const value = flushDraft(`class:${selectedClass.id}:attribute:${idx}:type`);
                        if (typeof value === 'string') updateAttributeField(idx, 'type', value);
                      }}
                    />
                    <Switch
                      size="small"
                      checked={attr.is_static}
                      onChange={(v) => {
                        const attrs = [...selectedClass.attributes];
                        attrs[idx] = { ...attrs[idx], is_static: v };
                        handleClassChange('attributes', attrs);
                      }}
                      title="static"
                    />
                    <Button
                      type="text" size="small" danger
                      icon={<MinusCircleOutlined />}
                      onClick={() => {
                        const attrs = selectedClass.attributes.filter((_, i) => i !== idx);
                        handleClassChange('attributes', attrs);
                      }}
                    />
                  </div>
                ))}
                <Button
                  type="dashed" size="small" block
                  icon={<PlusOutlined />}
                  onClick={() => {
                    const attrs = [...selectedClass.attributes, {
                      name: '', type: '', visibility: Visibility.PUBLIC, is_static: false,
                    }];
                    handleClassChange('attributes', attrs);
                  }}
                >
                  添加属性
                </Button>
              </div>
            ),
          }]}
        />

        {/* Methods */}
        <Collapse
          ghost
          defaultActiveKey={['methods']}
          items={[{
            key: 'methods',
            label: `方法 (${selectedClass.methods.length})`,
            children: (
              <div>
                {selectedClass.methods.map((method, idx) => (
                  <div key={idx} className="property-row method-row">
                    <Select
                      value={method.visibility}
                      size="small"
                      style={{ width: 50 }}
                      onChange={(v) => {
                        const methods = [...selectedClass.methods];
                        methods[idx] = { ...methods[idx], visibility: v };
                        handleClassChange('methods', methods);
                      }}
                      options={Object.values(Visibility).map((v) => ({ value: v, label: v }))}
                    />
                    <Input
                      size="small"
                      style={{ width: 80 }}
                      value={draftValue(`class:${selectedClass.id}:method:${idx}:name`, method.name)}
                      placeholder="方法名"
                      onChange={(e) => {
                        const value = e.target.value;
                        scheduleDraft(`class:${selectedClass.id}:method:${idx}:name`, value,
                          () => updateMethodField(idx, 'name', value));
                      }}
                      onBlur={() => {
                        const value = flushDraft(`class:${selectedClass.id}:method:${idx}:name`);
                        if (typeof value === 'string') updateMethodField(idx, 'name', value);
                      }}
                    />
                    <span className="attr-colon">(</span>
                    <Input
                      size="small"
                      style={{ width: 70 }}
                      value={draftValue(`class:${selectedClass.id}:method:${idx}:params`, method.params)}
                      placeholder="参数"
                      onChange={(e) => {
                        const value = e.target.value;
                        scheduleDraft(`class:${selectedClass.id}:method:${idx}:params`, value,
                          () => updateMethodField(idx, 'params', value));
                      }}
                      onBlur={() => {
                        const value = flushDraft(`class:${selectedClass.id}:method:${idx}:params`);
                        if (typeof value === 'string') updateMethodField(idx, 'params', value);
                      }}
                    />
                    <span className="attr-colon">):</span>
                    <Input
                      size="small"
                      style={{ width: 70 }}
                      value={draftValue(`class:${selectedClass.id}:method:${idx}:return_type`, method.return_type)}
                      placeholder="返回"
                      onChange={(e) => {
                        const value = e.target.value;
                        scheduleDraft(`class:${selectedClass.id}:method:${idx}:return_type`, value,
                          () => updateMethodField(idx, 'return_type', value));
                      }}
                      onBlur={() => {
                        const value = flushDraft(`class:${selectedClass.id}:method:${idx}:return_type`);
                        if (typeof value === 'string') updateMethodField(idx, 'return_type', value);
                      }}
                    />
                    <Button
                      type="text" size="small" danger
                      icon={<MinusCircleOutlined />}
                      onClick={() => {
                        const methods = selectedClass.methods.filter((_, i) => i !== idx);
                        handleClassChange('methods', methods);
                      }}
                    />
                  </div>
                ))}
                <Button
                  type="dashed" size="small" block
                  icon={<PlusOutlined />}
                  onClick={() => {
                    const methods = [...selectedClass.methods, {
                      name: '', return_type: 'void', params: '',
                      visibility: Visibility.PUBLIC, is_static: false, is_abstract: false,
                    }];
                    handleClassChange('methods', methods);
                  }}
                >
                  添加方法
                </Button>
              </div>
            ),
          }]}
        />
      </div>
    );
  }

  // ── Relation Property Editor ───────────────────────
  if (selectedRelation) {
    const srcClass = diagram.classes.find((c) => c.id === selectedRelation.source);
    const tgtClass = diagram.classes.find((c) => c.id === selectedRelation.target);

    const handleRelChange = (field: string, value: unknown) => {
      updateRelation(selectedRelation.id, { [field]: value });
    };

    return (
      <div className="property-panel">
        <div className="property-panel-header">
          <h3>连接属性</h3>
          <Popconfirm
            title="确认删除此连接？"
            onConfirm={() => removeRelation(selectedRelation.id)}
            okText="删除" cancelText="取消"
          >
            <Button danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </div>

        <div className="relation-summary">
          {srcClass?.name || selectedRelation.source}
          {' → '}
          {tgtClass?.name || selectedRelation.target}
        </div>

        <Form layout="vertical" size="small">
          <Form.Item label="关系类型">
            <Select
              value={selectedRelation.type}
              onChange={(v) => handleRelChange('type', v)}
              options={Object.values(RelationType).map((t) => ({
                value: t, label: t,
              }))}
            />
          </Form.Item>
          <Form.Item label="源多重性">
            <Input
              value={draftValue(`relation:${selectedRelation.id}:multiplicity_source`, selectedRelation.multiplicity_source)}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`relation:${selectedRelation.id}:multiplicity_source`, value,
                  () => updateRelation(selectedRelation.id, { multiplicity_source: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`relation:${selectedRelation.id}:multiplicity_source`);
                if (typeof value === 'string') updateRelation(selectedRelation.id, { multiplicity_source: value });
              }}
              placeholder="如: 0..1, 1..*, *"
            />
          </Form.Item>
          <Form.Item label="目标多重性">
            <Input
              value={draftValue(`relation:${selectedRelation.id}:multiplicity_target`, selectedRelation.multiplicity_target)}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`relation:${selectedRelation.id}:multiplicity_target`, value,
                  () => updateRelation(selectedRelation.id, { multiplicity_target: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`relation:${selectedRelation.id}:multiplicity_target`);
                if (typeof value === 'string') updateRelation(selectedRelation.id, { multiplicity_target: value });
              }}
              placeholder="如: 0..1, 1..*, *"
            />
          </Form.Item>
          <Form.Item label="角色名">
            <Input
              value={draftValue(`relation:${selectedRelation.id}:role_name`, selectedRelation.role_name)}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`relation:${selectedRelation.id}:role_name`, value,
                  () => updateRelation(selectedRelation.id, { role_name: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`relation:${selectedRelation.id}:role_name`);
                if (typeof value === 'string') updateRelation(selectedRelation.id, { role_name: value });
              }}
              placeholder="角色名称"
            />
          </Form.Item>
          <Form.Item label="连接备注">
            <TextArea
              value={draftValue(`relation:${selectedRelation.id}:note`, selectedRelation.note)}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`relation:${selectedRelation.id}:note`, value,
                  () => updateRelation(selectedRelation.id, { note: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`relation:${selectedRelation.id}:note`);
                if (typeof value === 'string') updateRelation(selectedRelation.id, { note: value });
              }}
              rows={2}
              placeholder="添加备注..."
            />
          </Form.Item>
        </Form>
      </div>
    );
  }

  // ── Message Property Editor ───────────────────────────
  if (selectedMessage) {
    const srcName = (diagram.lifelines || []).find((l) => l.id === selectedMessage.from_lifeline)?.name || '?';
    const tgtName = (diagram.lifelines || []).find((l) => l.id === selectedMessage.to_lifeline)?.name || '?';

    return (
      <div className="property-panel">
        <div className="property-panel-header">
          <h3>消息属性</h3>
          <Popconfirm
            title="确认删除此消息？"
            onConfirm={() => removeMessage(selectedMessage.id)}
            okText="删除" cancelText="取消"
          >
            <Button danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </div>

        <div className="relation-summary">{srcName} → {tgtName}</div>

        <Form layout="vertical" size="small">
          <Form.Item label="方法名">
            <Input
              value={draftValue(`message:${selectedMessage.id}:label`, selectedMessage.label)}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`message:${selectedMessage.id}:label`, value,
                  () => updateMessage(selectedMessage.id, { label: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`message:${selectedMessage.id}:label`);
                if (typeof value === 'string') updateMessage(selectedMessage.id, { label: value });
              }}
            />
          </Form.Item>
          <Form.Item label="消息类型">
            <Select
              value={selectedMessage.type}
              onChange={(v) => updateMessage(selectedMessage.id, { type: v })}
              options={[
                { value: 'sync', label: '→ 同步消息' },
                { value: 'async', label: '⇢ 异步消息' },
                { value: 'return', label: '-->> 返回消息' },
                { value: 'simple', label: '→ 简单消息' },
                { value: 'self', label: '↻ 自反消息' },
              ]}
            />
          </Form.Item>
          <Form.Item label="功能备注">
            <Input.TextArea
              value={draftValue(`message:${selectedMessage.id}:note`, selectedMessage.note || '')}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`message:${selectedMessage.id}:note`, value,
                  () => updateMessage(selectedMessage.id, { note: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`message:${selectedMessage.id}:note`);
                if (typeof value === 'string') updateMessage(selectedMessage.id, { note: value });
              }}
              rows={2}
              placeholder="描述此消息的业务含义..."
            />
          </Form.Item>
        </Form>
      </div>
    );
  }

  // ── Lifeline Property Editor ─────────────────────────
  if (selectedLifeline) {
    const handleChange = (field: string, value: unknown) => {
      updateLifeline(selectedLifeline.id, { [field]: value });
    };

    return (
      <div className="property-panel">
        <div className="property-panel-header">
          <h3>生命线属性</h3>
          <Popconfirm
            title="确认删除此生命线？关联的消息也会被删除"
            onConfirm={() => removeLifeline(selectedLifeline.id)}
            okText="删除" cancelText="取消"
          >
            <Button danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </div>

        <Form layout="vertical" size="small">
          <Form.Item label="名称">
            <Input
              value={draftValue(`lifeline:${selectedLifeline.id}:name`, selectedLifeline.name)}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`lifeline:${selectedLifeline.id}:name`, value,
                  () => updateLifeline(selectedLifeline.id, { name: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`lifeline:${selectedLifeline.id}:name`);
                if (typeof value === 'string') updateLifeline(selectedLifeline.id, { name: value });
              }}
              placeholder="如: ota: OtaTask"
            />
          </Form.Item>
          <Form.Item label="关联类（可选）">
            <Input
              value={draftValue(`lifeline:${selectedLifeline.id}:class_ref`, selectedLifeline.class_ref || '')}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`lifeline:${selectedLifeline.id}:class_ref`, value,
                  () => updateLifeline(selectedLifeline.id, { class_ref: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`lifeline:${selectedLifeline.id}:class_ref`);
                if (typeof value === 'string') updateLifeline(selectedLifeline.id, { class_ref: value });
              }}
              placeholder="UML 类图中类的名称"
            />
          </Form.Item>
        </Form>

        <Divider orientation="left" plain style={{ fontSize: 12 }}>
          激活条 ({selectedLifeline.activations?.length || 0} 个)
        </Divider>
        <p style={{ fontSize: 11, color: '#888' }}>
          激活条在创建消息时自动添加。删除消息不会自动移除激活条（可手动清理）。
        </p>
      </div>
    );
  }

  // ── Component Property Editor ────────────────────────
  if (selectedComponent) {
    const handleChange = (field: string, value: unknown) => {
      updateComponent(selectedComponent.id, { [field]: value });
    };

    return (
      <div className="property-panel">
        <div className="property-panel-header">
          <h3>组件属性</h3>
          <Popconfirm title="确认删除此组件？" onConfirm={() => removeComponent(selectedComponent.id)}
            okText="删除" cancelText="取消">
            <Button danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </div>
        <Form layout="vertical" size="small">
          <Form.Item label="名称">
            <Input
              value={draftValue(`component:${selectedComponent.id}:name`, selectedComponent.name)}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`component:${selectedComponent.id}:name`, value,
                  () => updateComponent(selectedComponent.id, { name: value }));
              }}
              onBlur={() => {
                const value = flushDraft(`component:${selectedComponent.id}:name`);
                if (typeof value === 'string') updateComponent(selectedComponent.id, { name: value });
              }}
            />
          </Form.Item>
          <Form.Item label="提供的接口（每行一个）">
            <Input.TextArea
              value={draftValue(`component:${selectedComponent.id}:provided_interfaces`,
                (selectedComponent.provided_interfaces || []).join('\n'))}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`component:${selectedComponent.id}:provided_interfaces`, value,
                  () => updateComponent(selectedComponent.id, {
                    provided_interfaces: value.split('\n').filter((s) => s.trim()),
                  }));
              }}
              onBlur={() => {
                const value = flushDraft(`component:${selectedComponent.id}:provided_interfaces`);
                if (typeof value === 'string') updateComponent(selectedComponent.id, {
                  provided_interfaces: value.split('\n').filter((s) => s.trim()),
                });
              }}
              rows={2} placeholder="IService&#10;IRepository" />
          </Form.Item>
          <Form.Item label="依赖的接口（每行一个）">
            <Input.TextArea
              value={draftValue(`component:${selectedComponent.id}:required_interfaces`,
                (selectedComponent.required_interfaces || []).join('\n'))}
              onChange={(e) => {
                const value = e.target.value;
                scheduleDraft(`component:${selectedComponent.id}:required_interfaces`, value,
                  () => updateComponent(selectedComponent.id, {
                    required_interfaces: value.split('\n').filter((s) => s.trim()),
                  }));
              }}
              onBlur={() => {
                const value = flushDraft(`component:${selectedComponent.id}:required_interfaces`);
                if (typeof value === 'string') updateComponent(selectedComponent.id, {
                  required_interfaces: value.split('\n').filter((s) => s.trim()),
                });
              }}
              rows={2} placeholder="IDatabase&#10;ILogger" />
          </Form.Item>
        </Form>

        {/* Linked diagrams */}
        {(() => {
          const linkedClass = project.diagrams.filter(
            (d) => d.component_id === selectedComponent.id && (d.diagram_type || 'class') === 'class'
          );
          const linkedSeq = project.diagrams.filter(
            (d) => d.component_id === selectedComponent.id && d.diagram_type === 'sequence'
          );
          return (
            <>
              <Divider orientation="left" plain style={{ fontSize: 12 }}>
                关联图 ({linkedClass.length + linkedSeq.length})
              </Divider>
              {linkedClass.length === 0 && linkedSeq.length === 0 && (
                <p style={{ fontSize: 12, color: '#bbb' }}>暂无关联的类图或时序图</p>
              )}
              {linkedClass.map((d) => (
                <div key={d.name} style={{
                  padding: '4px 8px', cursor: 'pointer', fontSize: 12,
                  borderRadius: 4, display: 'flex', alignItems: 'center', gap: 6,
                  marginBottom: 2,
                }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = '#f0f5ff')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  onClick={() => {
                    const idx = project.diagrams.indexOf(d);
                    if (idx >= 0) setActiveDiagram(idx);
                  }}
                >
                  📋 {d.name}
                </div>
              ))}
              {linkedSeq.map((d) => (
                <div key={d.name} style={{
                  padding: '4px 8px', cursor: 'pointer', fontSize: 12,
                  borderRadius: 4, display: 'flex', alignItems: 'center', gap: 6,
                  marginBottom: 2,
                }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = '#f0f5ff')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  onClick={() => {
                    const idx = project.diagrams.indexOf(d);
                    if (idx >= 0) setActiveDiagram(idx);
                  }}
                >
                  ⏱️ {d.name}
                </div>
              ))}
              <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
                <Button size="small" type="dashed" style={{ fontSize: 11 }}
                  onClick={() => addDiagram('class', `${selectedComponent.name}_class`, selectedComponent.id)}
                >+ 类图</Button>
                <Button size="small" type="dashed" style={{ fontSize: 11 }}
                  onClick={() => addDiagram('sequence', `${selectedComponent.name}_seq`, selectedComponent.id)}
                >+ 时序图</Button>
              </div>
            </>
          );
        })()}
      </div>
    );
  }

  // ── Nothing selected ───────────────────────────────
  return (
    <div className="property-panel">
      <Empty
        description="选择类或连接以编辑属性"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
      <div className="property-hints">
        <p><strong>提示:</strong></p>
        <ul>
          <li>双击画布空白区域添加类</li>
          <li>从节点端口拖拽创建连接</li>
          <li>Ctrl+滚轮缩放画布</li>
          <li>空格/中键拖拽平移</li>
          <li>Ctrl+Z 撤销 | Ctrl+Y 重做</li>
        </ul>
      </div>
    </div>
  );
};

export default PropertyPanel;

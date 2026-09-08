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
import { getActiveDiagram, selectActiveDiagram, useDiagramStore } from '../../stores/diagramStore';
import { useShallow } from 'zustand/react/shallow';
import {
  Visibility, Stereotype, RelationType,
  type UmlAttribute, type UmlMethod,
} from '../../types/uml';
import { useUiStore } from '../../stores/uiStore';
import { getPropertyLabels } from './propertyLabels';
import { useDebouncedDraft } from '../../hooks/useDebouncedDraft';
import './PropertyPanel.css';

const { TextArea } = Input;

const PropertyPanel: React.FC = () => {
  const interfaceLanguage = useUiStore((state) => state.interfaceLanguage);
  const labels = getPropertyLabels(interfaceLanguage);
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
    diagram: selectActiveDiagram(s),
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
      const liveClass = getActiveDiagram().classes.find((item) => item.id === selectedClass.id);
      if (!liveClass?.attributes[index]) return;
      const attributes = [...liveClass.attributes];
      attributes[index] = { ...attributes[index], [field]: value };
      updateClass(selectedClass.id, { attributes });
    };
    const updateMethodField = (index: number, field: string, value: unknown) => {
      const liveClass = getActiveDiagram().classes.find((item) => item.id === selectedClass.id);
      if (!liveClass?.methods[index]) return;
      const methods = [...liveClass.methods];
      methods[index] = { ...methods[index], [field]: value };
      updateClass(selectedClass.id, { methods });
    };

    return (
      <div className="property-panel">
        <div className="property-panel-header">
          <h3>{labels.class.title}</h3>
          <Popconfirm
            title={labels.class.deleteConfirm}
            onConfirm={() => removeClass(selectedClass.id)}
            okText={labels.common.delete} cancelText={labels.common.cancel}
          >
            <Button danger size="small" icon={<DeleteOutlined />}>{labels.common.delete}</Button>
          </Popconfirm>
        </div>

        <Form layout="vertical" size="small">
          <Form.Item label={labels.class.className}>
            <Input
              id={`class-${selectedClass.id}-name`}
              name="class-name"
              aria-label={labels.class.className}
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
          <Form.Item label={labels.class.stereotype}>
            <Select
              aria-label={labels.class.stereotype}
              value={selectedClass.stereotype}
              onChange={(v) => handleClassChange('stereotype', v)}
              options={Object.values(Stereotype).map((s) => ({ value: s, label: s }))}
            />
          </Form.Item>
          <Form.Item label={labels.common.note}>
            <TextArea
              id={`class-${selectedClass.id}-note`}
              name="class-note"
              aria-label={labels.common.note}
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
              placeholder={labels.common.addNote}
            />
          </Form.Item>
          <Form.Item label={`${labels.common.providedInterfaces} (◉ provided)`}>
            <TextArea
              id={`class-${selectedClass.id}-provided-interfaces`}
              name="provided-interfaces"
              aria-label={labels.common.providedInterfaces}
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
          <Form.Item label={`${labels.common.requiredInterfaces} (◡ required)`}>
            <TextArea
              id={`class-${selectedClass.id}-required-interfaces`}
              name="required-interfaces"
              aria-label={labels.common.requiredInterfaces}
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
            label: labels.class.attributes(selectedClass.attributes.length),
            children: (
              <div>
                {selectedClass.attributes.map((attr, idx) => (
                  <div key={idx} className="property-row">
                    <Select
                      aria-label={labels.class.attributeVisibility(idx + 1)}
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
                      id={`class-${selectedClass.id}-attribute-${idx}-name`}
                      name={`attribute-${idx}-name`}
                      aria-label={labels.class.attributeName(idx + 1)}
                      size="small"
                      style={{ width: 80 }}
                      value={draftValue(`class:${selectedClass.id}:attribute:${idx}:name`, attr.name)}
                      placeholder={labels.class.attributeNamePlaceholder}
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
                      id={`class-${selectedClass.id}-attribute-${idx}-type`}
                      name={`attribute-${idx}-type`}
                      aria-label={labels.class.attributeType(idx + 1)}
                      size="small"
                      style={{ width: 80 }}
                      value={draftValue(`class:${selectedClass.id}:attribute:${idx}:type`, attr.type)}
                      placeholder={labels.class.attributeTypePlaceholder}
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
                      aria-label={labels.class.staticMember(idx + 1)}
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
                      aria-label={labels.class.deleteAttribute(idx + 1)}
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
                  {labels.class.addAttribute}
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
            label: labels.class.methods(selectedClass.methods.length),
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
                      placeholder={labels.class.methodName}
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
                      placeholder={labels.class.params}
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
                      placeholder={labels.class.returnType}
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
                  {labels.class.addMethod}
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
          <h3>{labels.relation.title}</h3>
          <Popconfirm
            title={labels.relation.deleteConfirm}
            onConfirm={() => removeRelation(selectedRelation.id)}
            okText={labels.common.delete} cancelText={labels.common.cancel}
          >
            <Button danger size="small" icon={<DeleteOutlined />}>{labels.common.delete}</Button>
          </Popconfirm>
        </div>

        <div className="relation-summary">
          {srcClass?.name || selectedRelation.source}
          {' → '}
          {tgtClass?.name || selectedRelation.target}
        </div>

        <Form layout="vertical" size="small">
          <Form.Item label={labels.relation.type}>
            <Select
              value={selectedRelation.type}
              onChange={(v) => handleRelChange('type', v)}
              options={Object.values(RelationType).map((t) => ({
                value: t, label: t,
              }))}
            />
          </Form.Item>
          <Form.Item label={labels.relation.sourceMultiplicity}>
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
              placeholder={labels.relation.multiplicityPlaceholder}
            />
          </Form.Item>
          <Form.Item label={labels.relation.targetMultiplicity}>
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
              placeholder={labels.relation.multiplicityPlaceholder}
            />
          </Form.Item>
          <Form.Item label={labels.relation.roleName}>
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
              placeholder={labels.relation.roleNamePlaceholder}
            />
          </Form.Item>
          <Form.Item label={labels.relation.note}>
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
              placeholder={labels.common.addNote}
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
          <h3>{labels.message.title}</h3>
          <Popconfirm
            title={labels.message.deleteConfirm}
            onConfirm={() => removeMessage(selectedMessage.id)}
            okText={labels.common.delete} cancelText={labels.common.cancel}
          >
            <Button danger size="small" icon={<DeleteOutlined />}>{labels.common.delete}</Button>
          </Popconfirm>
        </div>

        <div className="relation-summary">{srcName} → {tgtName}</div>

        <Form layout="vertical" size="small">
          <Form.Item label={labels.message.methodName}>
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
          <Form.Item label={labels.message.type}>
            <Select
              value={selectedMessage.type}
              onChange={(v) => updateMessage(selectedMessage.id, { type: v })}
              options={[
                { value: 'sync', label: labels.message.typeLabels.sync },
                { value: 'async', label: labels.message.typeLabels.async },
                { value: 'return', label: labels.message.typeLabels.return },
                { value: 'simple', label: labels.message.typeLabels.simple },
                { value: 'self', label: labels.message.typeLabels.self },
              ]}
            />
          </Form.Item>
          <Form.Item label={labels.message.note}>
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
              placeholder={labels.message.notePlaceholder}
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
          <h3>{labels.lifeline.title}</h3>
          <Popconfirm
            title={labels.lifeline.deleteConfirm}
            onConfirm={() => removeLifeline(selectedLifeline.id)}
            okText={labels.common.delete} cancelText={labels.common.cancel}
          >
            <Button danger size="small" icon={<DeleteOutlined />}>{labels.common.delete}</Button>
          </Popconfirm>
        </div>

        <Form layout="vertical" size="small">
          <Form.Item label={labels.lifeline.name}>
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
              placeholder={labels.lifeline.namePlaceholder}
            />
          </Form.Item>
          <Form.Item label={labels.lifeline.linkedClass}>
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
              placeholder={labels.lifeline.linkedClassPlaceholder}
            />
          </Form.Item>
        </Form>

        <Divider orientation="left" plain style={{ fontSize: 12 }}>
          {labels.lifeline.activations(selectedLifeline.activations?.length || 0)}
        </Divider>
        <p style={{ fontSize: 11, color: '#888' }}>
          {labels.lifeline.activationHint}
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
          <h3>{labels.component.title}</h3>
          <Popconfirm title={labels.component.deleteConfirm} onConfirm={() => removeComponent(selectedComponent.id)}
            okText={labels.common.delete} cancelText={labels.common.cancel}>
            <Button danger size="small" icon={<DeleteOutlined />}>{labels.common.delete}</Button>
          </Popconfirm>
        </div>
        <Form layout="vertical" size="small">
          <Form.Item label={labels.common.name}>
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
          <Form.Item label={labels.component.providedInterfaces}>
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
          <Form.Item label={labels.component.requiredInterfaces}>
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
                {labels.component.linkedDiagrams(linkedClass.length + linkedSeq.length)}
              </Divider>
              {linkedClass.length === 0 && linkedSeq.length === 0 && (
                <p style={{ fontSize: 12, color: '#bbb' }}>{labels.component.noLinkedDiagrams}</p>
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
                >{labels.component.classDiagram}</Button>
                <Button size="small" type="dashed" style={{ fontSize: 11 }}
                  onClick={() => addDiagram('sequence', `${selectedComponent.name}_seq`, selectedComponent.id)}
                >{labels.component.sequenceDiagram}</Button>
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
        description={labels.common.noSelection}
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
      <div className="property-hints">
        <p><strong>{labels.common.hint}</strong></p>
        <ul>
          <li>{labels.common.connectHint}</li>
          <li>{labels.common.zoomHint}</li>
          <li>{labels.common.panHint}</li>
          <li>{labels.common.undoHint}</li>
        </ul>
      </div>
    </div>
  );
};

export default PropertyPanel;

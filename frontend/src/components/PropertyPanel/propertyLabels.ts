import type { InterfaceLanguage } from '../../i18n';

export interface PropertyLabels {
  common: {
    delete: string;
    cancel: string;
    name: string;
    note: string;
    addNote: string;
    providedInterfaces: string;
    requiredInterfaces: string;
    noSelection: string;
    hint: string;
    connectHint: string;
    zoomHint: string;
    panHint: string;
    undoHint: string;
  };
  class: {
    title: string;
    deleteConfirm: string;
    className: string;
    stereotype: string;
    attributes: (count: number) => string;
    attributeVisibility: (index: number) => string;
    attributeName: (index: number) => string;
    attributeType: (index: number) => string;
    staticMember: (index: number) => string;
    deleteAttribute: (index: number) => string;
    attributeNamePlaceholder: string;
    attributeTypePlaceholder: string;
    addAttribute: string;
    methods: (count: number) => string;
    methodName: string;
    params: string;
    returnType: string;
    addMethod: string;
  };
  relation: {
    title: string;
    deleteConfirm: string;
    type: string;
    sourceMultiplicity: string;
    targetMultiplicity: string;
    roleName: string;
    roleNamePlaceholder: string;
    note: string;
    multiplicityPlaceholder: string;
  };
  message: {
    title: string;
    deleteConfirm: string;
    methodName: string;
    type: string;
    note: string;
    notePlaceholder: string;
    typeLabels: Record<'sync' | 'async' | 'return' | 'simple' | 'self', string>;
  };
  lifeline: {
    title: string;
    deleteConfirm: string;
    name: string;
    namePlaceholder: string;
    linkedClass: string;
    linkedClassPlaceholder: string;
    activations: (count: number) => string;
    activationHint: string;
  };
  component: {
    title: string;
    deleteConfirm: string;
    providedInterfaces: string;
    requiredInterfaces: string;
    linkedDiagrams: (count: number) => string;
    noLinkedDiagrams: string;
    classDiagram: string;
    sequenceDiagram: string;
  };
}

export function getPropertyLabels(language: InterfaceLanguage): PropertyLabels {
  if (language === 'en') {
    return {
      common: {
        delete: 'Delete', cancel: 'Cancel', name: 'Name', note: 'Note', addNote: 'Add a note...',
        providedInterfaces: 'Provided interfaces (one per line)',
        requiredInterfaces: 'Required interfaces (one per line)',
        noSelection: 'Select a class or connection to edit its properties', hint: 'Tips:',
        connectHint: 'Drag from a node port to create a connection',
        zoomHint: 'Ctrl + mouse wheel to zoom', panHint: 'Space / middle-button drag to pan',
        undoHint: 'Ctrl + Z Undo | Ctrl + Y Redo',
      },
      class: {
        title: 'Class properties', deleteConfirm: 'Delete this class?', className: 'Class name',
        stereotype: 'Stereotype', attributes: (count) => `Attributes (${count})`,
        attributeVisibility: (index) => `Attribute ${index} visibility`, attributeName: (index) => `Attribute ${index} name`,
        attributeType: (index) => `Attribute ${index} type`, staticMember: (index) => `Attribute ${index} static member`,
        deleteAttribute: (index) => `Delete attribute ${index}`, attributeNamePlaceholder: 'Name',
        attributeTypePlaceholder: 'Type', addAttribute: 'Add attribute', methods: (count) => `Methods (${count})`,
        methodName: 'Method name', params: 'Parameters', returnType: 'Return type', addMethod: 'Add method',
      },
      relation: {
        title: 'Connection properties', deleteConfirm: 'Delete this connection?', type: 'Relationship type',
        sourceMultiplicity: 'Source multiplicity', targetMultiplicity: 'Target multiplicity', roleName: 'Role name',
        roleNamePlaceholder: 'Role name', note: 'Connection note', multiplicityPlaceholder: 'e.g. 0..1, 1..*, *',
      },
      message: {
        title: 'Message properties', deleteConfirm: 'Delete this message?', methodName: 'Method name', type: 'Message type',
        note: 'Functional note', notePlaceholder: 'Describe the business meaning of this message...',
        typeLabels: { sync: '→ Synchronous message', async: '⇢ Asynchronous message', return: '-->> Return message', simple: '→ Simple message', self: '↻ Self message' },
      },
      lifeline: {
        title: 'Lifeline properties', deleteConfirm: 'Delete this lifeline? Related messages will also be deleted', name: 'Name',
        namePlaceholder: 'e.g. ota: OtaTask', linkedClass: 'Linked class (optional)', linkedClassPlaceholder: 'Class name in the UML class diagram',
        activations: (count) => `Activation bars (${count})`, activationHint: 'Activation bars are added when messages are created. Deleting a message does not remove them automatically (clean up manually).',
      },
      component: {
        title: 'Component properties', deleteConfirm: 'Delete this component?', providedInterfaces: 'Provided interfaces (one per line)',
        requiredInterfaces: 'Required interfaces (one per line)', linkedDiagrams: (count) => `Linked diagrams (${count})`,
        noLinkedDiagrams: 'No linked class or sequence diagrams', classDiagram: '+ Class diagram', sequenceDiagram: '+ Sequence diagram',
      },
    };
  }

  return {
    common: {
      delete: '删除', cancel: '取消', name: '名称', note: '备注', addNote: '添加备注...',
      providedInterfaces: '提供的接口（每行一个）', requiredInterfaces: '依赖的接口（每行一个）',
      noSelection: '选择类或连接以编辑属性', hint: '提示:',
      connectHint: '从节点端口拖拽创建连接', zoomHint: 'Ctrl+滚轮缩放画布', panHint: '空格/中键拖拽平移',
      undoHint: 'Ctrl+Z 撤销 | Ctrl+Y 重做',
    },
    class: {
      title: '类属性', deleteConfirm: '确认删除此类？', className: '类名', stereotype: '构造型',
      attributes: (count) => `属性 (${count})`, attributeVisibility: (index) => `属性 ${index} 可见性`,
      attributeName: (index) => `属性 ${index} 名称`, attributeType: (index) => `属性 ${index} 类型`,
      staticMember: (index) => `属性 ${index} 静态成员`, deleteAttribute: (index) => `删除属性 ${index}`,
      attributeNamePlaceholder: '名称', attributeTypePlaceholder: '类型', addAttribute: '添加属性',
      methods: (count) => `方法 (${count})`, methodName: '方法名', params: '参数', returnType: '返回', addMethod: '添加方法',
    },
    relation: {
      title: '连接属性', deleteConfirm: '确认删除此连接？', type: '关系类型', sourceMultiplicity: '源多重性',
      targetMultiplicity: '目标多重性', roleName: '角色名', roleNamePlaceholder: '角色名称', note: '连接备注',
      multiplicityPlaceholder: '如: 0..1, 1..*, *',
    },
    message: {
      title: '消息属性', deleteConfirm: '确认删除此消息？', methodName: '方法名', type: '消息类型', note: '功能备注',
      notePlaceholder: '描述此消息的业务含义...',
      typeLabels: { sync: '→ 同步消息', async: '⇢ 异步消息', return: '-->> 返回消息', simple: '→ 简单消息', self: '↻ 自反消息' },
    },
    lifeline: {
      title: '生命线属性', deleteConfirm: '确认删除此生命线？关联的消息也会被删除', name: '名称', namePlaceholder: '如: ota: OtaTask',
      linkedClass: '关联类（可选）', linkedClassPlaceholder: 'UML 类图中类的名称', activations: (count) => `激活条 (${count} 个)`,
      activationHint: '激活条在创建消息时自动添加。删除消息不会自动移除激活条（可手动清理）。',
    },
    component: {
      title: '组件属性', deleteConfirm: '确认删除此组件？', providedInterfaces: '提供的接口（每行一个）',
      requiredInterfaces: '依赖的接口（每行一个）', linkedDiagrams: (count) => `关联图 (${count})`,
      noLinkedDiagrams: '暂无关联的类图或时序图', classDiagram: '+ 类图', sequenceDiagram: '+ 时序图',
    },
  };
}

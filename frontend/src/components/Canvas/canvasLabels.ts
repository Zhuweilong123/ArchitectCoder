import type { InterfaceLanguage } from '../../i18n';
import type { FragmentType, MessageType } from '../../types/sequence';

export interface CanvasLabels {
  classDiagram: {
    add: string;
    autoLayout: string;
    autoLayoutTitle: string;
    selected: (count: number) => string;
    alignLeft: string;
    alignCenter: string;
    alignRight: string;
    alignTop: string;
    alignMiddle: string;
    alignBottom: string;
    distributeHorizontal: string;
    distributeVertical: string;
    showToolbar: string;
    hideToolbar: string;
    attributes: string;
    operations: string;
  };
  componentDiagram: {
    add: string;
    addTitle: string;
    autoLayout: string;
    autoLayoutTitle: string;
    showToolbar: string;
    hideToolbar: string;
    providedInterfaces: string;
    requiredInterfaces: string;
    linkedClassDiagrams: (count: number) => string;
    linkedSequenceDiagrams: (count: number) => string;
    noLinkedClassDiagrams: string;
    noLinkedSequenceDiagrams: string;
    createClassDiagram: string;
    createSequenceDiagram: string;
  };
  sequenceDiagram: {
    addLifeline: string;
    addLifelineTitle: string;
    chooseMessageType: string;
    arrange: string;
    arrangeTitle: string;
    center: string;
    centerTitle: string;
    fitFragments: string;
    fitFragmentsTitle: string;
    fragments: string;
    addFragment: (name: string) => string;
    messageLegend: string;
    sync: string;
    async: string;
    return: string;
    self: string;
    showToolbar: string;
    hideToolbar: string;
    inCanvasEdit: string;
    addSelfMessage: string;
    sendToBack: string;
    bringToFront: string;
    selectedLifelineHint: string;
  };
}

const messageTypeNames: Record<InterfaceLanguage, Record<MessageType, string>> = {
  en: {
    sync: 'Synchronous message',
    async: 'Asynchronous message',
    return: 'Return message',
    simple: 'Simple message',
    self: 'Self message',
  },
  zh: {
    sync: '同步消息',
    async: '异步消息',
    return: '返回消息',
    simple: '简单消息',
    self: '自反消息',
  },
};

const fragmentNames: Record<InterfaceLanguage, Record<FragmentType, string>> = {
  en: {
    loop: 'loop', alt: 'alt', opt: 'opt', break: 'break',
    par: 'par', critical: 'critical', neg: 'neg',
  },
  zh: {
    loop: 'loop', alt: 'alt', opt: 'opt', break: 'break',
    par: 'par', critical: 'critical', neg: 'neg',
  },
};

export function getMessageTypeLabels(language: InterfaceLanguage): Record<MessageType, string> {
  return messageTypeNames[language];
}

export function getFragmentLabels(language: InterfaceLanguage): Record<FragmentType, string> {
  return fragmentNames[language];
}

export function getCanvasLabels(language: InterfaceLanguage): CanvasLabels {
  if (language === 'en') {
    return {
      classDiagram: {
        add: 'Class', autoLayout: 'Layout', autoLayoutTitle: 'Auto layout',
        selected: (count) => `${count} selected`, alignLeft: 'Align left',
        alignCenter: 'Align center', alignRight: 'Align right', alignTop: 'Align top',
        alignMiddle: 'Align middle', alignBottom: 'Align bottom',
        distributeHorizontal: 'Distribute horizontally', distributeVertical: 'Distribute vertically',
        showToolbar: 'Show canvas toolbar', hideToolbar: 'Hide canvas toolbar',
        attributes: 'Attributes', operations: 'Operations',
      },
      componentDiagram: {
        add: 'Component', addTitle: 'Create a child component when selected; otherwise create a top-level component',
        autoLayout: 'Layout', autoLayoutTitle: 'Arrange components by dependencies and organize the hierarchy',
        showToolbar: 'Show canvas toolbar', hideToolbar: 'Hide canvas toolbar',
        providedInterfaces: 'provided interfaces', requiredInterfaces: 'required interfaces',
        linkedClassDiagrams: (count) => `Linked class diagrams (${count})`,
        linkedSequenceDiagrams: (count) => `Linked sequence diagrams (${count})`,
        noLinkedClassDiagrams: 'No linked class diagrams',
        noLinkedSequenceDiagrams: 'No linked sequence diagrams',
        createClassDiagram: 'Create a class diagram for this component',
        createSequenceDiagram: 'Create a sequence diagram for this component',
      },
      sequenceDiagram: {
        addLifeline: 'Lifeline', addLifelineTitle: 'Add a lifeline',
        chooseMessageType: 'Choose a message type, then click the sender and receiver lifelines',
        arrange: 'Arrange', arrangeTitle: 'Evenly arrange lifelines and message timing',
        center: 'Center', centerTitle: 'Center the sequence diagram in the canvas',
        fitFragments: 'Fit fragments', fitFragmentsTitle: 'Fit loop, alt, and other fragment ranges to their messages',
        fragments: 'Fragments', addFragment: (name) => `Add ${name} fragment`,
        messageLegend: 'Message type legend', sync: 'Sync', async: 'Async', return: 'Return', self: 'Self',
        showToolbar: 'Show canvas toolbar', hideToolbar: 'Hide canvas toolbar',
        inCanvasEdit: 'Edit in canvas', addSelfMessage: 'Add self message',
        sendToBack: 'Send to back', bringToFront: 'Bring to front',
        selectedLifelineHint: '▼ Selected; click another lifeline to create a message ▼',
      },
    };
  }

  return {
    classDiagram: {
      add: '类', autoLayout: '布局', autoLayoutTitle: '自动布局',
      selected: (count) => `已选 ${count} 个`, alignLeft: '左对齐', alignCenter: '水平居中',
      alignRight: '右对齐', alignTop: '顶部对齐', alignMiddle: '垂直居中', alignBottom: '底部对齐',
      distributeHorizontal: '水平均匀分布', distributeVertical: '垂直均匀分布',
      showToolbar: '显示画布工具栏', hideToolbar: '隐藏画布工具栏',
      attributes: '属性', operations: '操作',
    },
    componentDiagram: {
      add: '组件', addTitle: '选中组件时创建子组件，未选中时创建顶层组件',
      autoLayout: '整理', autoLayoutTitle: '按依赖关系排列组件，并整理子组件层级',
      showToolbar: '显示画布工具栏', hideToolbar: '隐藏画布工具栏',
      providedInterfaces: '提供接口', requiredInterfaces: '需要接口',
      linkedClassDiagrams: (count) => `关联的类图 (${count})`,
      linkedSequenceDiagrams: (count) => `关联的时序图 (${count})`,
      noLinkedClassDiagrams: '暂无关联类图', noLinkedSequenceDiagrams: '暂无关联时序图',
      createClassDiagram: '为此组件新建类图', createSequenceDiagram: '为此组件新建时序图',
    },
    sequenceDiagram: {
      addLifeline: '生命线', addLifelineTitle: '添加生命线',
      chooseMessageType: '先选择消息类型，再依次点击发送方和接收方生命线',
      arrange: '整理', arrangeTitle: '均匀排列生命线并整理消息时间轴',
      center: '居中', centerTitle: '将时序图自动居中到可视画布',
      fitFragments: '适配片段', fitFragmentsTitle: '根据片段内消息自动调整 loop、alt 等片段范围',
      fragments: '片段', addFragment: (name) => `添加 ${name} 片段`,
      messageLegend: '消息类型图例', sync: '同步', async: '异步', return: '返回', self: '自反',
      showToolbar: '显示画布工具栏', hideToolbar: '隐藏画布工具栏',
      inCanvasEdit: '图内编辑', addSelfMessage: '添加自反消息',
      sendToBack: '置于底层', bringToFront: '置于上层',
      selectedLifelineHint: '▼ 已选中，点击另一生命线创建消息 ▼',
    },
  };
}

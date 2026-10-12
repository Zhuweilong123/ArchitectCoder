import type { CompNode, CompRelation } from './component';
import type { SeqFragment, SeqLifeline, SeqMessage } from './sequence';
import type { UmlClass, UmlDiagram, UmlRelation } from './uml';

export interface DiagramSpec {
  type: string;
  name: string;
  component_id: string;
  data: Partial<UmlDiagram>;
}
export type DiagramSnapshots = Record<string, UmlDiagram>;
export interface ConsistencyIssue {
  severity: 'error' | 'warning' | 'info';
  msg: string;
  auto_fixed?: boolean;
}
export type ReviewDiagramPayload = Partial<UmlDiagram> | DiagramSpec;
export interface StreamElementMap {
  diagram_create: { type: string; name: string; component_id: string };
  diagram_meta: { diagram_name: string };
  class: UmlClass & { diagram_name: string };
  relation: UmlRelation & { diagram_name: string };
  lifeline: SeqLifeline & { diagram_name: string };
  message: Omit<SeqMessage, 'y'> & { y?: number; diagram_name: string };
  fragment: SeqFragment & { diagram_name: string };
  component: CompNode & { diagram_name: string };
  comp_rel: CompRelation & { diagram_name: string };
  diagram_update: DiagramSpec;
}
export type DesignStreamElement = {
  [Kind in keyof StreamElementMap]: { type: Kind; data: StreamElementMap[Kind] }
}[keyof StreamElementMap];

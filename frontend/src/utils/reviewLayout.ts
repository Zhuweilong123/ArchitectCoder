import type { UmlDiagram } from '../types/uml';
import { layoutClasses } from './classLayout';
import { getClassContentLayout } from './classContentLayout';
import { layoutComponents } from './componentLayout';
import { arrangeSequenceLayout } from './sequenceLayout';

/** Prepare one immutable candidate for both the review preview and canvas. */
export function layoutReviewDiagram(diagram: UmlDiagram): UmlDiagram {
  if (diagram.diagram_type === 'sequence') {
    return { ...diagram, ...arrangeSequenceLayout(
      diagram.lifelines || [], diagram.messages || [], diagram.fragments || [],
    ) };
  }
  if (diagram.diagram_type === 'component') {
    return layoutComponents(diagram) || diagram;
  }
  const classes = diagram.classes.map((cls) => ({
    ...cls, size: { ...cls.size, height: getClassContentLayout(cls).height },
  }));
  const positions = layoutClasses({ ...diagram, classes });
  return {
    ...diagram,
    classes: classes.map((cls) => ({ ...cls, position: positions.get(cls.id) || cls.position })),
    relations: diagram.relations.map((relation) => ({ ...relation, vertices: undefined })),
  };
}

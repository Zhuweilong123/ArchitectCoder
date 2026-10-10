import type { TraceCaseDraft, TraceCaseTurn } from '../../services/api';

export function draftTurns(draft: TraceCaseDraft): TraceCaseTurn[] {
  const turns = draft.case.turns?.length ? draft.case.turns : [{ prompt: draft.case.prompt, checkers: [], hard_checkers: [], metadata: {} }];
  return turns.map((turn, index) => ({
    ...turn,
    checkers: turn.checkers || [],
    hard_checkers: turn.hard_checkers || [],
    metadata: { ...turn.metadata, source_turn_index: turn.metadata?.source_turn_index || index + 1 },
  }));
}

export function reviewPayload(
  name: string, projectId: string, checkers: Array<Record<string, any>>,
  hardCheckers: Array<Record<string, any>>, turns: TraceCaseTurn[], confirmed: boolean,
) {
  return { name: name.trim(), project_id: projectId, checkers, hard_checkers: hardCheckers, turns, fixture_state_confirmed: confirmed };
}

export function savedReviewPayload(draft: TraceCaseDraft) {
  return reviewPayload(draft.case.name, draft.case.project_id, draft.case.checkers || [],
    draft.case.hard_checkers || [], draftTurns(draft), Boolean(draft.capture?.baseline_confirmed));
}

export function hasHardCriteria(hard: Array<Record<string, any>>, turns: TraceCaseTurn[]): boolean {
  return hard.length > 0 || turns.some((turn) => turn.hard_checkers.length > 0);
}

export function selectTurns(indices: number[], edited: TraceCaseTurn[], source: TraceCaseTurn[]): TraceCaseTurn[] {
  return [...new Set(indices)].sort((a, b) => a - b).flatMap((index) => {
    const turn = edited.find((item) => Number(item.metadata.source_turn_index) === index)
      || source.find((item) => Number(item.metadata.source_turn_index) === index);
    return turn ? [turn] : [];
  });
}

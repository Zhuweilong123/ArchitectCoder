import {
  getEvalBaseline, getEvalRepository, listEvalArchives, listEvalCases,
  listEvalPerformanceResults, listEvalTrends,
  listTraceCaseDrafts, listTraceCaseProjects, listTraces,
} from './api';

export async function loadEvaluationOverview() {
  const [cases, baseline, repository, trends, archives, performanceRuns] = await Promise.all([
    listEvalCases(),
    getEvalBaseline(),
    getEvalRepository(),
    listEvalTrends(),
    listEvalArchives(),
    listEvalPerformanceResults(),
  ]);
  return { cases, baseline, repository, trends, archives, performanceRuns };
}

export async function loadTraceCaseFactoryResources() {
  const [traces, projects, drafts] = await Promise.all([
    listTraces(),
    listTraceCaseProjects(),
    listTraceCaseDrafts(),
  ]);
  return { traces, projects, drafts };
}

import {
  openDiagram, openProject, saveProject,
} from './api';
import type { Project, UmlDiagram } from '../types/uml';

export function openToolbarProject(path: string, safe = true): Promise<Project> {
  return openProject(path, safe);
}

export function openToolbarDiagram(path: string, safe = true): Promise<UmlDiagram> {
  return openDiagram(path, safe);
}

export function saveToolbarProject(
  project: Project,
  path: string,
  safe = true,
): ReturnType<typeof saveProject> {
  return saveProject(project, path, safe);
}

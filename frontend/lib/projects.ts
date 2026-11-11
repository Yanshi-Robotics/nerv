import type { SessionSummary } from './api';
const BASE = process.env.NEXT_PUBLIC_API ?? 'http://localhost:8170';
export type ProjectLayout = { primary?: string; hidden?: string[]; monitorSize?: string; dock?: string };
export type ProjectRun = { brain_instance_id?: string; robot_instance_id?: string; world_instance_id?: string; client_id?: string; shared_world?: boolean; id: string; session_id: string; status: string; created_at?: number | string; reason?: string };
export type RobotResource = { robot_instance_id:string; brain_instance_id:string; world_instance_id:string; project_id:string; status:string; client_id?:string; shared_world:boolean };
export type WorldInstance = {world_instance_id:string; world:string; robot_profile:string; status:string; generation:number; topology_revision:number; topology_policy?:{allow_agent_changes?:boolean}; roster:{robot_instance_id:string;project_id:string;project_name?:string}[]};
export type Project = { resource?:RobotResource|null; id: string; name: string; mode: 'sim'|'real'; robot: string; world?: string|null; execution: string; scheme?: string; revision: number; created_at: number | string; updated_at?: number | string; last_opened_at?: number | string; view_layout?: ProjectLayout; active_run?: ProjectRun|null; readiness?: {ready: boolean; reasons?: string[]} };
export type ProjectDetail = { project: Project; runs: ProjectRun[]; active_run: ProjectRun|null; readiness?: {ready: boolean; issues: {code: string; reason?: string; destination: string}[]} };
export type ProjectDraft = { name: string; mode: 'sim'|'real'; robot: string; world: string; execution: string; scheme: string };
export type AgentConnection = { id: string; name: string; projects: string[]; permissions: string[]; world_grants?:{world_instance_id:string}[]; revoked?: boolean; last_seen?: string; supervision?: string };
export async function projectRequest<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}/api/${path}`, {method, headers: body === undefined ? undefined : {'Content-Type':'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
export const listProjects = async (): Promise<Project[]> => {const data = await projectRequest('projects'); return Array.isArray(data) ? data : data.projects;};
export const getProject = (id: string) => projectRequest<ProjectDetail>(`projects/${encodeURIComponent(id)}`);
export const startProject = (id: string) => projectRequest<{run: ProjectRun; session: SessionSummary}>(`projects/${encodeURIComponent(id)}/runs`, 'POST', {});
export const saveProjectLayout = (id: string, expected_revision: number, view_layout: ProjectLayout) => projectRequest(`projects/${encodeURIComponent(id)}`, 'PATCH', {expected_revision,view_layout});
export function projectRoute(hash: string): string {
  let value: string;
  try {value = decodeURIComponent(hash.replace(/^#/, ''));} catch {return 'projects';}
  if (!value || ['home', 'home/new', 'new'].includes(value)) return 'projects';
  if (value === 'settings') return 'settings/general';
  return value;
}
export function projectForSession(projects: Project[], sid: string): Project|undefined {return projects.find(p => p.active_run?.session_id === sid);}

/** Filter by actual execution contract, not matching robot dimensions alone. */
export function schemeMatchesExecution(execution: string, template: string): boolean {
  if (execution === 'act') return template === 'fixed_policy';
  if (execution === 'managed') return false;
  return (execution === 'external_vlm' || execution === 'external_skills') && template === execution;
}

/** Host timestamps use Unix seconds; archived imports may use ISO dates. */
export function projectDate(value: number | string | undefined): Date | null {
  if (value === undefined) return null;
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export type ContributionMode = 'observer' | 'transform' | 'control' | 'service';

export interface PluginContribution {
  id: string;
  stage: string;
  handler: string;
  mode: ContributionMode;
  priority: number;
  before: string[];
  after: string[];
  scope: string;
  fail_closed: boolean;
  plugin: string;
  order: number;
  interface_id?: string;
}

export interface PluginStage {
  stage: string;
  supported_modes: ContributionMode[];
  contributions: PluginContribution[];
}

export interface PlanPlugin {
  name: string;
  provider: string;
  source: string;
  status: 'discovered' | 'disabled' | 'unavailable';
  error: string;
  interfaces: string[];
  contributions: string[];
  interface_bindings?: Array<{ method: string; stage: string; contribution_id: string }>;
  version?: string;
  revision?: string;
  manifest_digest?: string;
  implementation_digest?: string;
  diagnostics?: PluginDiagnostic[];
  slot?: string;
  dependencies?: string[];
  optional_dependencies?: string[];
  config_keys?: string[];
}

export interface PluginDiagnostic {
  plugin: string;
  component: string;
  phase: string;
  code: string;
  message: string;
  provider: string;
  source: string;
  version: string;
  revision: string;
}

export interface PluginLoadReport {
  plan_id: string;
  plugins: Array<{ name: string; status: string; version: string; revision: string; diagnostics: PluginDiagnostic[] }>;
}

export interface PluginExecutionPlan {
  plan_id: string;
  schema_version: number;
  dispatch: string;
  plugins: PlanPlugin[];
  stages: PluginStage[];
  notifications?: PluginStage[];
  run_end_semantics?: string;
}

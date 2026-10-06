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
  slot?: string;
  dependencies?: string[];
  optional_dependencies?: string[];
  config_keys?: string[];
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

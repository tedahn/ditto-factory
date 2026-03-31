// ---- Enums ----

export enum ThreadStatus {
  IDLE = "idle",
  RUNNING = "running",
  QUEUED = "queued",
}

export enum JobStatus {
  PENDING = "pending",
  RUNNING = "running",
  COMPLETED = "completed",
  FAILED = "failed",
}

export enum TaskType {
  CODE_CHANGE = "code_change",
  ANALYSIS = "analysis",
  DB_MUTATION = "db_mutation",
  FILE_OUTPUT = "file_output",
  API_ACTION = "api_action",
}

export enum ResultType {
  PULL_REQUEST = "pull_request",
  REPORT = "report",
  DB_ROWS = "db_rows",
  FILE_ARTIFACT = "file_artifact",
  API_RESPONSE = "api_response",
}

export enum ResolutionReason {
  BEST_MATCH = "best_match",
  DEFAULT_FALLBACK = "default_fallback",
  OVERRIDE = "override",
}

// ---- Core Models ----

export interface Artifact {
  name: string;
  path: string;
  content_type: string;
  size: number;
  url?: string;
}

export interface TaskRequest {
  thread_id: string;
  source: string;
  source_ref: Record<string, unknown>;
  repo_owner: string;
  repo_name: string;
  task: string;
  conversation: string[];
  images: string[];
  skill_overrides?: string[] | null;
  agent_type_override?: string | null;
  task_type: TaskType;
  template_slug?: string | null;
  workflow_parameters: Record<string, unknown>;
  toolkit_slugs?: string[];
  component_slugs?: string[];
}

export interface AgentResult {
  branch: string;
  exit_code: number;
  commit_count: number;
  stderr: string;
  pr_url?: string | null;
  trace_events: Record<string, unknown>[];
  result_type: ResultType;
  artifacts: Artifact[];
}

export interface Thread {
  id: string;
  source: string;
  source_ref: Record<string, unknown>;
  repo_owner: string;
  repo_name: string;
  status: ThreadStatus;
  current_job_name?: string | null;
  conversation_history: Record<string, unknown>[];
  created_at?: string | null;
  updated_at?: string | null;
}

export interface Job {
  id: string;
  thread_id: string;
  k8s_job_name: string;
  status: JobStatus;
  task_context: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  agent_type: string;
  skills_injected: string[];
  started_at?: string | null;
  completed_at?: string | null;
}

// ---- Skill Types ----

export interface Skill {
  id: string;
  name: string;
  slug: string;
  description: string;
  content: string;
  tags: string[];
  language?: string | null;
  domain?: string | null;
  version: number;
  usage_count?: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SkillCreateRequest {
  name: string;
  slug: string;
  description: string;
  content: string;
  tags: string[];
  language?: string;
  domain?: string;
}

export interface SkillUpdateRequest {
  name?: string;
  description?: string;
  content?: string;
  tags?: string[];
  language?: string;
  domain?: string;
  changelog?: string;
}

export interface SkillSearchRequest {
  query?: string;
  tags?: string[];
  language?: string;
  domain?: string;
  limit?: number;
  min_similarity?: number;
}

export interface SkillSearchResult {
  skills: Array<{
    slug: string;
    name: string;
    similarity: number;
    usage_count: number;
    success_rate: number;
  }>;
}

export interface SkillVersion {
  version: number;
  changelog: string;
  created_by: string;
  created_at: string;
}

// ---- Workflow Types ----

export interface WorkflowStep {
  id: string;
  name: string;
  task_type: TaskType;
  task_template: string;
  depends_on: string[];
  skill_overrides?: string[];
  parameters: Record<string, unknown>;
}

export interface WorkflowTemplate {
  id: string;
  slug: string;
  name: string;
  description: string;
  definition: Record<string, unknown>;
  parameter_schema?: Record<string, unknown> | null;
  steps: WorkflowStep[];
  version?: number;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface WorkflowExecutionStep {
  name: string;
  status: string;
  agent_type?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  result_summary?: string | null;
  depends_on?: string[];
}

export interface WorkflowExecution {
  execution_id: string;
  template_slug: string;
  status: string;
  parameters?: Record<string, unknown>;
  steps: WorkflowExecutionStep[];
  started_at?: string | null;
  completed_at?: string | null;
  triggered_by?: string | null;
}

export interface TemplateCreateRequest {
  slug: string;
  name: string;
  description: string;
  definition: Record<string, unknown>;
  parameter_schema?: Record<string, unknown>;
  created_by?: string;
}

export interface TemplateUpdateRequest {
  definition?: Record<string, unknown>;
  parameter_schema?: Record<string, unknown>;
  description?: string;
  changelog?: string;
  updated_by?: string;
}

export interface ExecutionCreateRequest {
  template_slug: string;
  parameters: Record<string, unknown>;
  triggered_by?: string;
}

export interface WorkflowEstimate {
  total_steps: number;
  parallel_groups: number;
  estimated_duration_seconds: number;
  estimated_agents: number;
}

export interface TemplateVersion {
  version: number;
  changelog: string;
  created_by: string;
  created_at: string;
}

// ---- Toolkit Enums ----

export enum ToolkitCategory {
  SKILL_COLLECTION = "skill_collection",
  PLUGIN = "plugin",
  PROFILE_PACK = "profile_pack",
  TOOL = "tool",
  MIXED = "mixed",
}

export enum ComponentType {
  SKILL = "skill",
  PLUGIN = "plugin",
  PROFILE = "profile",
  TOOL = "tool",
  AGENT = "agent",
  COMMAND = "command",
}

export enum LoadStrategy {
  MOUNT_FILE = "mount_file",
  INSTALL_PLUGIN = "install_plugin",
  INJECT_RULES = "inject_rules",
  INSTALL_PACKAGE = "install_package",
}

export enum RiskLevel {
  SAFE = "safe",
  MODERATE = "moderate",
  HIGH = "high",
}

export enum ToolkitStatus {
  AVAILABLE = "available",
  DISABLED = "disabled",
  UPDATE_AVAILABLE = "update_available",
  ERROR = "error",
}

// ---- Toolkit Models ----

export interface ToolkitSource {
  id: string;
  github_url: string;
  github_owner: string;
  github_repo: string;
  branch: string;
  last_commit_sha: string | null;
  last_synced_at: string | null;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
  toolkit_count: number;
}

export interface Toolkit {
  id: string;
  source_id: string;
  slug: string;
  name: string;
  category: ToolkitCategory;
  description: string;
  version: number;
  pinned_sha: string | null;
  source_version: string | null;
  status: ToolkitStatus;
  tags: string[];
  component_count: number;
  created_at: string | null;
  updated_at: string | null;
  source_owner: string | null;
  source_repo: string | null;
  source_branch: string | null;
}

export interface ToolkitDetail extends Toolkit {
  components: ToolkitComponentSummary[];
}

export interface ToolkitComponentSummary {
  id: string;
  slug: string;
  name: string;
  type: ComponentType;
  description: string;
  directory: string;
  primary_file: string;
  load_strategy: LoadStrategy;
  tags: string[];
  risk_level: RiskLevel;
  is_active: boolean;
  file_count: number;
}

export interface ToolkitComponentDetail extends ToolkitComponentSummary {
  content: string;
  files: ComponentFile[];
}

export interface ComponentFile {
  id: string;
  path: string;
  filename: string;
  is_primary: boolean;
}

export interface ToolkitVersion {
  id: string;
  version: number;
  pinned_sha: string;
  changelog: string | null;
  created_at: string | null;
}

// Discovery types
export interface DiscoveredFile {
  path: string;
  filename: string;
  is_primary: boolean;
}

export interface DiscoveredComponent {
  name: string;
  type: ComponentType;
  directory: string;
  primary_file: string;
  load_strategy: LoadStrategy;
  description: string;
  tags: string[];
  risk_level: RiskLevel;
  files: DiscoveredFile[];
}

export interface DiscoveryManifest {
  source_url: string;
  owner: string;
  repo: string;
  branch: string;
  commit_sha: string;
  repo_description: string;
  category: ToolkitCategory;
  discovered: DiscoveredComponent[];
  source_id: string | null;
}

// ---- GitHub Token ----

export interface GitHubTokenStatus {
  configured: boolean;
  rate_limit: number | null;
  rate_remaining: number | null;
  scopes: string | null;
}

// ---- Dashboard ----

export interface DashboardSummary {
  threads: {
    total: number;
    by_status: Record<ThreadStatus, number>;
  };
  jobs: {
    total: number;
    by_status: Record<JobStatus, number>;
    recent: Job[];
  };
  skills: {
    total: number;
  };
  workflows: {
    total: number;
    active_executions: number;
  };
}

// ---- Agent Types ----

export interface CandidateInfo {
  name: string;
  capabilities: string[];
  coverage: number;
  covers_all: boolean;
}

export interface ResolutionEvent {
  thread_id: string;
  timestamp: string | null;
  required_capabilities: string[];
  candidates_considered: CandidateInfo[];
  selected: string;
  reason: ResolutionReason;
}

export interface AgentTypeSummary {
  id: string;
  name: string;
  image: string;
  description: string | null;
  capabilities: string[];
  is_default: boolean;
  created_at: string | null;
  job_count: number;
  recent_resolutions: ResolutionEvent[];
  mapped_skills: string[];
}

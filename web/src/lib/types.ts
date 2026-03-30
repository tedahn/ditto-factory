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
  steps: WorkflowStep[];
  created_at?: string | null;
  updated_at?: string | null;
}

export interface WorkflowExecution {
  id: string;
  template_id: string;
  status: JobStatus;
  step_results: Record<string, unknown>;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface TemplateCreateRequest {
  slug: string;
  name: string;
  description: string;
  steps: Omit<WorkflowStep, "id">[];
}

export interface ExecutionCreateRequest {
  template_id: string;
  parameters: Record<string, unknown>;
  repo_owner: string;
  repo_name: string;
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

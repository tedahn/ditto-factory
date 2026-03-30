"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost, apiPut, apiDelete } from "./api";
import type {
  Thread,
  Job,
  TaskType,
  Skill,
  SkillCreateRequest,
  SkillUpdateRequest,
  SkillSearchRequest,
  SkillSearchResult,
  SkillVersion,
  WorkflowTemplate,
  WorkflowExecution,
  TemplateCreateRequest,
  TemplateUpdateRequest,
  ExecutionCreateRequest,
  WorkflowEstimate,
  TemplateVersion,
  ToolkitSource,
  Toolkit,
  ToolkitVersion,
  DiscoveryManifest,
  DiscoveredItem,
} from "./types";

// ---- Query Keys ----
export const queryKeys = {
  health: ["health"] as const,
  threads: ["threads"] as const,
  thread: (id: string) => ["threads", id] as const,
  jobs: (threadId: string) => ["threads", threadId, "jobs"] as const,
  job: (threadId: string, jobId: string) =>
    ["threads", threadId, "jobs", jobId] as const,
  taskDetail: (threadId: string) => ["tasks", threadId] as const,
  dashboardSummary: ["dashboard-summary"] as const,
  skills: ["skills"] as const,
  skillsFiltered: (params: Record<string, string>) =>
    ["skills", params] as const,
  skill: (slug: string) => ["skills", slug] as const,
  skillVersions: (slug: string) => ["skills", slug, "versions"] as const,
  skillSearch: ["skills", "search"] as const,
  // Toolkit keys
  toolkitSources: ["toolkit-sources"] as const,
  toolkits: ["toolkits"] as const,
  toolkit: (slug: string) => ["toolkits", slug] as const,
  toolkitVersions: (slug: string) => ["toolkits", slug, "versions"] as const,
  // Workflow keys
  workflowTemplates: ["workflow-templates"] as const,
  workflowTemplate: (slug: string) => ["workflow-templates", slug] as const,
  workflowTemplateVersions: (slug: string) =>
    ["workflow-templates", slug, "versions"] as const,
  workflowExecutions: ["workflow-executions"] as const,
  workflowExecution: (id: string) => ["workflow-executions", id] as const,
};

// ---- Health ----
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => apiGet<{ status: string }>("/health"),
    refetchInterval: 15_000,
    retry: 0,
  });
}

// ---- Threads ----
export function useThreads() {
  return useQuery({
    queryKey: queryKeys.threads,
    queryFn: () => apiGet<Thread[]>("/api/threads"),
    refetchInterval: 10_000,
  });
}

export function useThread(id: string) {
  return useQuery({
    queryKey: queryKeys.thread(id),
    queryFn: () => apiGet<Thread>(`/api/threads/${id}`),
    enabled: !!id,
  });
}

// ---- Jobs ----
export function useJobs(threadId: string) {
  return useQuery({
    queryKey: queryKeys.jobs(threadId),
    queryFn: () => apiGet<Job[]>(`/api/threads/${threadId}/jobs`),
    enabled: !!threadId,
  });
}

// ---- Task Submission ----
interface SubmitTaskInput {
  repo_owner: string;
  repo_name: string;
  task: string;
}

export function useSubmitTask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: SubmitTaskInput) =>
      apiPost("/api/tasks", {
        ...input,
        source: "web",
        source_ref: {},
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.threads });
    },
  });
}

// ---- Full Task Submission (with task_type, overrides, etc.) ----
interface SubmitTaskFullInput {
  repo_owner: string;
  repo_name: string;
  task: string;
  source: string;
  source_ref: Record<string, unknown>;
  task_type: TaskType;
  skill_overrides?: string[];
  template_slug?: string;
}

export function useSubmitTaskFull() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: SubmitTaskFullInput) =>
      apiPost<{ thread_id: string; job_name: string; status: string }>(
        "/api/tasks",
        input,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.threads });
    },
  });
}

// ---- Task Detail ----
export function useTaskDetail(threadId: string) {
  return useQuery({
    queryKey: queryKeys.taskDetail(threadId),
    queryFn: () =>
      apiGet<{
        thread_id: string;
        status: string;
        job?: Record<string, unknown>;
        conversation_history?: Record<string, unknown>[];
        result?: Record<string, unknown>;
        repo_owner?: string;
        repo_name?: string;
        created_at?: string;
        updated_at?: string;
        current_job_name?: string;
      }>(`/api/tasks/${threadId}`),
    enabled: !!threadId,
    refetchInterval: 5_000,
  });
}

// ---- Dashboard Summary ----

export interface DashboardSummaryData {
  active_count: number;
  completed_24h: number;
  failed_24h: number;
  avg_duration_seconds: number;
}

export function useDashboardSummary() {
  return useQuery({
    queryKey: queryKeys.dashboardSummary,
    queryFn: () => apiGet<DashboardSummaryData>("/api/dashboard"),
    refetchInterval: 10_000,
  });
}

// ---- Active Agents (running threads) ----

export function useActiveAgents() {
  return useQuery({
    queryKey: [...queryKeys.threads, "active"] as const,
    queryFn: async () => {
      const threads = await apiGet<Thread[]>("/api/threads");
      return threads.filter(
        (t) => t.status === "running" || t.status === "queued",
      );
    },
    refetchInterval: 5_000,
  });
}

// ---- Skills ----
export function useSkills(filters?: {
  tag?: string;
  language?: string;
  domain?: string;
}) {
  const params = new URLSearchParams();
  if (filters?.tag) params.set("tag", filters.tag);
  if (filters?.language) params.set("language", filters.language);
  if (filters?.domain) params.set("domain", filters.domain);
  const qs = params.toString();
  const path = `/api/v1/skills${qs ? `?${qs}` : ""}`;

  return useQuery({
    queryKey: qs
      ? queryKeys.skillsFiltered(Object.fromEntries(params))
      : queryKeys.skills,
    queryFn: async () => {
      const res = await apiGet<{ skills: Skill[]; total: number }>(path);
      return res.skills;
    },
    refetchInterval: 30_000,
  });
}

export function useSkill(slug: string) {
  return useQuery({
    queryKey: queryKeys.skill(slug),
    queryFn: () => apiGet<Skill>(`/api/v1/skills/${slug}`),
    enabled: !!slug,
  });
}

export function useCreateSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: SkillCreateRequest) =>
      apiPost<Skill>("/api/v1/skills", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.skills });
    },
  });
}

export function useUpdateSkill(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: SkillUpdateRequest) =>
      apiPut<Skill>(`/api/v1/skills/${slug}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.skills });
      queryClient.invalidateQueries({ queryKey: queryKeys.skill(slug) });
      queryClient.invalidateQueries({
        queryKey: queryKeys.skillVersions(slug),
      });
    },
  });
}

export function useDeleteSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => apiDelete(`/api/v1/skills/${slug}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.skills });
    },
  });
}

export function useSkillVersions(slug: string) {
  return useQuery({
    queryKey: queryKeys.skillVersions(slug),
    queryFn: () => apiGet<SkillVersion[]>(`/api/v1/skills/${slug}/versions`),
    enabled: !!slug,
  });
}

export function useRollbackSkill(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (targetVersion: number) =>
      apiPost<Skill>(`/api/v1/skills/${slug}/rollback`, {
        target_version: targetVersion,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.skill(slug) });
      queryClient.invalidateQueries({
        queryKey: queryKeys.skillVersions(slug),
      });
      queryClient.invalidateQueries({ queryKey: queryKeys.skills });
    },
  });
}

export function useSearchSkills() {
  return useMutation({
    mutationFn: (data: SkillSearchRequest) =>
      apiPost<SkillSearchResult>("/api/v1/skills/search", data),
  });
}

// ---- Workflow Templates ----

export function useWorkflowTemplates() {
  return useQuery({
    queryKey: queryKeys.workflowTemplates,
    queryFn: () =>
      apiGet<{ templates: WorkflowTemplate[]; total: number }>(
        "/api/v1/workflows/templates",
      ),
    refetchInterval: 30_000,
  });
}

export function useWorkflowTemplate(slug: string) {
  return useQuery({
    queryKey: queryKeys.workflowTemplate(slug),
    queryFn: () =>
      apiGet<WorkflowTemplate>(`/api/v1/workflows/templates/${slug}`),
    enabled: !!slug,
  });
}

export function useCreateWorkflowTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: TemplateCreateRequest) =>
      apiPost<WorkflowTemplate>("/api/v1/workflows/templates", data),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowTemplates,
      });
    },
  });
}

export function useUpdateWorkflowTemplate(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: TemplateUpdateRequest) =>
      apiPut<WorkflowTemplate>(`/api/v1/workflows/templates/${slug}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowTemplates,
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowTemplate(slug),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowTemplateVersions(slug),
      });
    },
  });
}

export function useDeleteWorkflowTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) =>
      apiDelete(`/api/v1/workflows/templates/${slug}`),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowTemplates,
      });
    },
  });
}

export function useWorkflowTemplateVersions(slug: string) {
  return useQuery({
    queryKey: queryKeys.workflowTemplateVersions(slug),
    queryFn: () =>
      apiGet<TemplateVersion[]>(
        `/api/v1/workflows/templates/${slug}/versions`,
      ),
    enabled: !!slug,
  });
}

export function useRollbackWorkflowTemplate(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (targetVersion?: number) =>
      apiPost<WorkflowTemplate>(
        `/api/v1/workflows/templates/${slug}/rollback`,
        targetVersion !== undefined ? { target_version: targetVersion } : {},
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowTemplate(slug),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowTemplateVersions(slug),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowTemplates,
      });
    },
  });
}

// ---- Workflow Executions ----

export function useWorkflowExecutions() {
  return useQuery({
    queryKey: queryKeys.workflowExecutions,
    queryFn: () =>
      apiGet<{ executions: WorkflowExecution[]; total: number }>(
        "/api/v1/workflows/executions",
      ),
    refetchInterval: 10_000,
  });
}

export function useWorkflowExecution(id: string) {
  return useQuery({
    queryKey: queryKeys.workflowExecution(id),
    queryFn: () =>
      apiGet<WorkflowExecution>(`/api/v1/workflows/executions/${id}`),
    enabled: !!id,
    refetchInterval: 5_000,
  });
}

export function useStartWorkflowExecution() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ExecutionCreateRequest) =>
      apiPost<WorkflowExecution>("/api/v1/workflows/executions", data),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowExecutions,
      });
    },
  });
}

export function useCancelWorkflowExecution() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiPost<void>(`/api/v1/workflows/executions/${id}/cancel`),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowExecution(id),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.workflowExecutions,
      });
    },
  });
}

export function useEstimateWorkflow() {
  return useMutation({
    mutationFn: (data: { template_slug: string; parameters: Record<string, unknown> }) =>
      apiPost<WorkflowEstimate>("/api/v1/workflows/estimate", data),
  });
}

// ---- Toolkit Sources ----

export function useToolkitSources() {
  return useQuery({
    queryKey: queryKeys.toolkitSources,
    queryFn: async () => {
      const res = await apiGet<{ sources: ToolkitSource[]; total: number }>(
        "/api/v1/toolkits/sources",
      );
      return res;
    },
    refetchInterval: 30_000,
  });
}

export function useCreateSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: { github_url: string; branch?: string }) =>
      apiPost<ToolkitSource>("/api/v1/toolkits/sources", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkitSources });
    },
  });
}

export function useDeleteSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiDelete(`/api/v1/toolkits/sources/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkitSources });
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkits });
    },
  });
}

export function useSyncSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiPost<ToolkitSource>(`/api/v1/toolkits/sources/${id}/sync`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkitSources });
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkits });
    },
  });
}

// ---- Toolkits ----

export function useToolkits(filters?: {
  type?: string;
  status?: string;
  source_id?: string;
}) {
  const params = new URLSearchParams();
  if (filters?.type) params.set("type", filters.type);
  if (filters?.status) params.set("status", filters.status);
  if (filters?.source_id) params.set("source_id", filters.source_id);
  const qs = params.toString();
  const path = `/api/v1/toolkits/${qs ? `?${qs}` : ""}`;

  return useQuery({
    queryKey: [...queryKeys.toolkits, filters ?? {}] as const,
    queryFn: async () => {
      const res = await apiGet<{ toolkits: Toolkit[]; total: number }>(path);
      return res;
    },
    refetchInterval: 30_000,
  });
}

export function useToolkit(slug: string) {
  return useQuery({
    queryKey: queryKeys.toolkit(slug),
    queryFn: () => apiGet<Toolkit>(`/api/v1/toolkits/${slug}`),
    enabled: !!slug,
  });
}

export function useToolkitVersions(slug: string) {
  return useQuery({
    queryKey: queryKeys.toolkitVersions(slug),
    queryFn: () =>
      apiGet<ToolkitVersion[]>(`/api/v1/toolkits/${slug}/versions`),
    enabled: !!slug,
  });
}

export function useDeleteToolkit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) =>
      apiDelete(`/api/v1/toolkits/${slug}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkits });
    },
  });
}

export function useRollbackToolkit(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (targetVersion: number) =>
      apiPost<Toolkit>(`/api/v1/toolkits/${slug}/rollback`, {
        target_version: targetVersion,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkit(slug) });
      queryClient.invalidateQueries({
        queryKey: queryKeys.toolkitVersions(slug),
      });
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkits });
    },
  });
}

export function useApplyToolkitUpdate(slug: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiPost<Toolkit>(`/api/v1/toolkits/${slug}/update`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkit(slug) });
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkits });
    },
  });
}

// ---- Toolkit Discovery & Import ----

export function useDiscover() {
  return useMutation({
    mutationFn: (data: { github_url: string; branch?: string }) =>
      apiPost<DiscoveryManifest>("/api/v1/toolkits/discover", data),
  });
}

export function useImportToolkits() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: { source_id: string; items: DiscoveredItem[] }) =>
      apiPost<{ imported: number }>("/api/v1/toolkits/import", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkits });
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkitSources });
    },
  });
}

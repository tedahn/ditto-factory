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
  skills: ["skills"] as const,
  skillsFiltered: (params: Record<string, string>) =>
    ["skills", params] as const,
  skill: (slug: string) => ["skills", slug] as const,
  skillVersions: (slug: string) => ["skills", slug, "versions"] as const,
  skillSearch: ["skills", "search"] as const,
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
    queryFn: () => apiGet<Skill[]>(path),
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

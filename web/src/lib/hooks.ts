"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "./api";
import type { Thread, Job } from "./types";

// ---- Query Keys ----
export const queryKeys = {
  health: ["health"] as const,
  threads: ["threads"] as const,
  thread: (id: string) => ["threads", id] as const,
  jobs: (threadId: string) => ["threads", threadId, "jobs"] as const,
  job: (threadId: string, jobId: string) =>
    ["threads", threadId, "jobs", jobId] as const,
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

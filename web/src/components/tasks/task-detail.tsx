"use client";

import { useMemo } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Clock,
  ExternalLink,
  FileText,
  GitBranch,
  Loader2,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useTaskDetail, useJobs } from "@/lib/hooks";
import { ThreadStatus, JobStatus } from "@/lib/types";
import type { Job } from "@/lib/types";

function statusBadgeVariant(status: string) {
  switch (status) {
    case ThreadStatus.RUNNING:
    case JobStatus.RUNNING:
      return "success" as const;
    case ThreadStatus.IDLE:
    case JobStatus.COMPLETED:
      return "info" as const;
    case ThreadStatus.QUEUED:
    case JobStatus.PENDING:
      return "warning" as const;
    case JobStatus.FAILED:
      return "destructive" as const;
    default:
      return "secondary" as const;
  }
}

function formatTimestamp(dateStr: string | null | undefined): string {
  if (!dateStr) return "--";
  const d = new Date(dateStr);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function TimelineEntry({
  label,
  time,
  status,
}: {
  label: string;
  time: string | null | undefined;
  status?: string;
}) {
  return (
    <div className="flex items-start gap-3 py-2">
      <div className="flex flex-col items-center">
        <div
          className={`h-2.5 w-2.5 rounded-full mt-1 ${
            status === "completed" || status === "idle"
              ? "bg-emerald-500"
              : status === "running"
                ? "bg-blue-500 animate-pulse"
                : status === "failed"
                  ? "bg-red-500"
                  : "bg-muted-foreground/40"
          }`}
        />
        <div className="w-px h-full bg-border/50 min-h-[16px]" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-foreground">{label}</p>
        <p className="text-xs font-mono text-muted-foreground">
          {formatTimestamp(time)}
        </p>
      </div>
    </div>
  );
}

function ArtifactsList({ result }: { result: Record<string, unknown> | null | undefined }) {
  if (!result) return null;

  const prUrl = result.pr_url as string | undefined;
  const artifacts = (result.artifacts as Array<{ name: string; url?: string; path: string }>) || [];
  const branch = result.branch as string | undefined;
  const exitCode = result.exit_code as number | undefined;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle>Results & Artifacts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {branch && (
          <div className="flex items-center gap-2 text-sm">
            <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-muted-foreground">Branch:</span>
            <code className="font-mono text-xs bg-secondary px-1.5 py-0.5 rounded">
              {branch}
            </code>
          </div>
        )}

        {exitCode !== undefined && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Exit code:</span>
            <Badge variant={exitCode === 0 ? "success" : "destructive"}>
              {exitCode}
            </Badge>
          </div>
        )}

        {prUrl && (
          <a
            href={prUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-sm text-blue-400 hover:text-blue-300 transition-colors"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Pull Request
          </a>
        )}

        {artifacts.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Artifacts
            </p>
            {artifacts.map((artifact, i) => (
              <div
                key={i}
                className="flex items-center gap-2 text-sm py-1.5 px-2 rounded bg-secondary/50"
              >
                <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                <span className="truncate font-mono text-xs">
                  {artifact.name || artifact.path}
                </span>
                {artifact.url && (
                  <a
                    href={artifact.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-auto shrink-0"
                  >
                    <ExternalLink className="h-3 w-3 text-muted-foreground hover:text-foreground" />
                  </a>
                )}
              </div>
            ))}
          </div>
        )}

        {!prUrl && artifacts.length === 0 && !branch && (
          <p className="text-sm text-muted-foreground">
            No results available yet.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function ConversationHistory({
  history,
}: {
  history: Record<string, unknown>[];
}) {
  if (!history || history.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle>Conversation History</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-3 max-h-96 overflow-y-auto">
          {history.map((entry, i) => {
            const role = (entry.role as string) || "system";
            const content = (entry.content as string) || JSON.stringify(entry);

            return (
              <div key={i} className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  {role}
                </p>
                <div className="text-sm text-foreground bg-secondary/30 rounded px-3 py-2 whitespace-pre-wrap font-mono text-xs leading-relaxed">
                  {typeof content === "string"
                    ? content
                    : JSON.stringify(content, null, 2)}
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

function JobInfo({ job }: { job: Job }) {
  return (
    <div className="space-y-2 py-2 border-b border-border/50 last:border-0">
      <div className="flex items-center gap-2">
        <Badge variant={statusBadgeVariant(job.status)}>{job.status}</Badge>
        <span className="text-xs font-mono text-muted-foreground">
          {job.k8s_job_name}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>Agent: {job.agent_type}</span>
        <span>Skills: {job.skills_injected.join(", ") || "none"}</span>
        <span>Started: {formatTimestamp(job.started_at)}</span>
        <span>Completed: {formatTimestamp(job.completed_at)}</span>
      </div>
    </div>
  );
}

interface TaskDetailProps {
  threadId: string;
}

export function TaskDetail({ threadId }: TaskDetailProps) {
  const {
    data: taskData,
    isLoading: taskLoading,
    isError: taskError,
  } = useTaskDetail(threadId);
  const { data: jobs } = useJobs(threadId);

  const timeline = useMemo(() => {
    const entries: { label: string; time: string | null | undefined; status?: string }[] = [];
    if (taskData) {
      entries.push({
        label: "Task created",
        time: (taskData as { created_at?: string }).created_at || null,
        status: "completed",
      });
    }
    if (jobs) {
      for (const job of jobs) {
        if (job.started_at) {
          entries.push({
            label: `Job ${job.k8s_job_name} started`,
            time: job.started_at,
            status: "running",
          });
        }
        if (job.completed_at) {
          entries.push({
            label: `Job ${job.k8s_job_name} ${job.status}`,
            time: job.completed_at,
            status: job.status,
          });
        }
      }
    }
    entries.sort((a, b) => {
      if (!a.time) return -1;
      if (!b.time) return 1;
      return a.time.localeCompare(b.time);
    });
    return entries;
  }, [taskData, jobs]);

  if (taskLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">
          Loading task details...
        </span>
      </div>
    );
  }

  if (taskError || !taskData) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-destructive-foreground">
          Failed to load task details.
        </p>
        <Link href="/tasks">
          <Button variant="outline" className="mt-4">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Tasks
          </Button>
        </Link>
      </div>
    );
  }

  const detail = taskData as {
    thread_id: string;
    status: string;
    job?: Record<string, unknown>;
    conversation_history?: Record<string, unknown>[];
    result?: Record<string, unknown>;
    // Thread fields that may also be present
    repo_owner?: string;
    repo_name?: string;
    created_at?: string;
    updated_at?: string;
    current_job_name?: string;
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link href="/tasks">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4 mr-1" />
            Back
          </Button>
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-foreground">
              {detail.repo_owner && detail.repo_name
                ? `${detail.repo_owner}/${detail.repo_name}`
                : "Task Detail"}
            </h2>
            <Badge variant={statusBadgeVariant(detail.status)}>
              {detail.status}
            </Badge>
          </div>
          <p className="text-xs font-mono text-muted-foreground mt-0.5">
            {detail.thread_id}
          </p>
        </div>
      </div>

      {/* Two-column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Left: Task info + timeline */}
        <div className="lg:col-span-2 space-y-6">
          {/* Task Info */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle>Task Info</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-1 gap-2 text-sm">
                {detail.current_job_name && (
                  <div>
                    <span className="text-muted-foreground">Current Job:</span>
                    <code className="ml-2 font-mono text-xs bg-secondary px-1.5 py-0.5 rounded">
                      {detail.current_job_name}
                    </code>
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-muted-foreground">Created:</span>
                  <span className="font-mono text-xs">
                    {formatTimestamp(detail.created_at)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-muted-foreground">Updated:</span>
                  <span className="font-mono text-xs">
                    {formatTimestamp(detail.updated_at)}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Timeline */}
          {timeline.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle>Timeline</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-0">
                  {timeline.map((entry, i) => (
                    <TimelineEntry
                      key={i}
                      label={entry.label}
                      time={entry.time}
                      status={entry.status}
                    />
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Jobs */}
          {jobs && jobs.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle>Jobs ({jobs.length})</CardTitle>
              </CardHeader>
              <CardContent>
                {jobs.map((job) => (
                  <JobInfo key={job.id} job={job} />
                ))}
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right: Results + conversation */}
        <div className="lg:col-span-3 space-y-6">
          <ArtifactsList result={detail.result || (detail.job as Record<string, unknown>)?.result as Record<string, unknown> | null} />
          <ConversationHistory
            history={detail.conversation_history || []}
          />
        </div>
      </div>
    </div>
  );
}

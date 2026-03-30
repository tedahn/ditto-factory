"use client";

import { useMemo } from "react";
import { Activity, GitBranch, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useThreads } from "@/lib/hooks";
import { ThreadStatus } from "@/lib/types";
import type { Thread } from "@/lib/types";

function statusBadgeVariant(status: ThreadStatus) {
  switch (status) {
    case ThreadStatus.RUNNING:
      return "success" as const;
    case ThreadStatus.IDLE:
      return "info" as const;
    case ThreadStatus.QUEUED:
      return "warning" as const;
    default:
      return "secondary" as const;
  }
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);

  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

function ActivityRow({ thread }: { thread: Thread }) {
  return (
    <div className="flex items-center gap-3 py-2.5 px-1 border-b border-border/50 last:border-0">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-secondary">
        <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-foreground truncate">
            {thread.repo_owner}/{thread.repo_name}
          </span>
          <Badge variant={statusBadgeVariant(thread.status)}>
            {thread.status}
          </Badge>
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-xs font-mono text-muted-foreground truncate">
            {thread.id.slice(0, 8)}
          </span>
          {thread.current_job_name && (
            <span className="text-xs text-muted-foreground truncate">
              {thread.current_job_name}
            </span>
          )}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <span className="text-xs font-mono text-muted-foreground">
          {thread.updated_at
            ? formatRelativeTime(thread.updated_at)
            : thread.created_at
              ? formatRelativeTime(thread.created_at)
              : "--"}
        </span>
      </div>
    </div>
  );
}

export function ActivityFeed() {
  const { data: threads, isLoading, isError } = useThreads();

  const recentThreads = useMemo(() => {
    if (!threads) return [];
    return [...threads]
      .sort((a, b) => {
        const aTime = a.updated_at || a.created_at || "";
        const bTime = b.updated_at || b.created_at || "";
        return bTime.localeCompare(aTime);
      })
      .slice(0, 20);
  }, [threads]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <CardTitle>Recent Activity</CardTitle>
          {threads && (
            <span className="text-xs text-muted-foreground font-mono ml-auto">
              {threads.length} total
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            <span className="ml-2 text-sm text-muted-foreground">
              Loading activity...
            </span>
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <p className="text-sm text-destructive-foreground">
              Failed to load activity. Is the controller running?
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Ensure the API is reachable at /api/proxy
            </p>
          </div>
        ) : recentThreads.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <Activity className="mb-2 h-8 w-8 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">
              No activity yet. Submit a task to get started.
            </p>
          </div>
        ) : (
          <div className="divide-y-0">
            {recentThreads.map((thread) => (
              <ActivityRow key={thread.id} thread={thread} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

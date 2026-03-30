"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpDown, Loader2 } from "lucide-react";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { ThreadStatus } from "@/lib/types";
import type { Thread } from "@/lib/types";

type SortField = "repo" | "status" | "created_at" | "updated_at";
type SortDir = "asc" | "desc";

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

function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return "--";
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

function getDuration(thread: Thread): string {
  if (!thread.created_at) return "--";
  const start = new Date(thread.created_at).getTime();
  const end = thread.updated_at
    ? new Date(thread.updated_at).getTime()
    : Date.now();
  const diffSec = Math.floor((end - start) / 1000);
  if (diffSec < 60) return `${diffSec}s`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m`;
  const diffHr = Math.floor(diffMin / 60);
  return `${diffHr}h ${diffMin % 60}m`;
}

function getTaskSummary(thread: Thread): string {
  if (
    thread.conversation_history &&
    thread.conversation_history.length > 0
  ) {
    const first = thread.conversation_history[0];
    const content =
      (first as Record<string, string>).content ||
      (first as Record<string, string>).task ||
      "";
    if (typeof content === "string" && content.length > 0) {
      return content.length > 80 ? content.slice(0, 80) + "..." : content;
    }
  }
  return thread.current_job_name || "No description";
}

interface TaskTableProps {
  threads: Thread[];
  isLoading: boolean;
  isError: boolean;
  statusFilter: string;
  repoFilter: string;
}

export function TaskTable({
  threads,
  isLoading,
  isError,
  statusFilter,
  repoFilter,
}: TaskTableProps) {
  const [sortField, setSortField] = useState<SortField>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  const filtered = useMemo(() => {
    if (!threads) return [];
    let result = [...threads];

    if (statusFilter && statusFilter !== "all") {
      result = result.filter((t) => t.status === statusFilter);
    }

    if (repoFilter) {
      const lower = repoFilter.toLowerCase();
      result = result.filter(
        (t) =>
          t.repo_name.toLowerCase().includes(lower) ||
          t.repo_owner.toLowerCase().includes(lower),
      );
    }

    result.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "repo":
          cmp = `${a.repo_owner}/${a.repo_name}`.localeCompare(
            `${b.repo_owner}/${b.repo_name}`,
          );
          break;
        case "status":
          cmp = a.status.localeCompare(b.status);
          break;
        case "created_at":
          cmp = (a.created_at || "").localeCompare(b.created_at || "");
          break;
        case "updated_at":
          cmp = (a.updated_at || "").localeCompare(b.updated_at || "");
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });

    return result;
  }, [threads, statusFilter, repoFilter, sortField, sortDir]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">
          Loading tasks...
        </span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-destructive-foreground">
          Failed to load tasks. Is the controller running?
        </p>
      </div>
    );
  }

  if (filtered.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">
          {threads.length === 0
            ? "No tasks yet. Submit a task to get started."
            : "No tasks match the current filters."}
        </p>
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="w-[100px]">
            <button
              onClick={() => toggleSort("status")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Status
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead>
            <button
              onClick={() => toggleSort("repo")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Repository
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="hidden md:table-cell">Summary</TableHead>
          <TableHead className="w-[120px]">
            <button
              onClick={() => toggleSort("created_at")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Created
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="w-[100px] hidden sm:table-cell">
            Duration
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {filtered.map((thread) => (
          <TableRow key={thread.id}>
            <TableCell>
              <Badge variant={statusBadgeVariant(thread.status)}>
                {thread.status}
              </Badge>
            </TableCell>
            <TableCell>
              <Link
                href={`/tasks/${thread.id}`}
                className="text-sm font-medium text-foreground hover:underline"
              >
                {thread.repo_owner}/{thread.repo_name}
              </Link>
              <div className="text-xs font-mono text-muted-foreground mt-0.5">
                {thread.id.slice(0, 8)}
              </div>
            </TableCell>
            <TableCell className="hidden md:table-cell">
              <Link
                href={`/tasks/${thread.id}`}
                className="text-sm text-muted-foreground hover:text-foreground transition-colors"
              >
                {getTaskSummary(thread)}
              </Link>
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {formatRelativeTime(thread.created_at)}
              </span>
            </TableCell>
            <TableCell className="hidden sm:table-cell">
              <span className="text-xs font-mono text-muted-foreground">
                {getDuration(thread)}
              </span>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

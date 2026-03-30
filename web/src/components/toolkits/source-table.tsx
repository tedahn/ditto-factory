"use client";

import { useState } from "react";
import { RefreshCw, Trash2, GitBranch, ExternalLink, Loader2 } from "lucide-react";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ToolkitSource } from "@/lib/types";

function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return "Never";
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

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-500/15 text-green-400 border-green-500/20",
  disabled: "bg-gray-500/15 text-gray-400 border-gray-500/20",
  error: "bg-red-500/15 text-red-400 border-red-500/20",
};

interface SourceTableProps {
  sources: ToolkitSource[];
  isLoading: boolean;
  isError: boolean;
  onSync?: (id: string) => void;
  onDelete?: (id: string) => void;
  syncingId?: string | null;
}

export function SourceTable({
  sources,
  isLoading,
  isError,
  onSync,
  onDelete,
  syncingId,
}: SourceTableProps) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">
          Loading sources...
        </span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-destructive-foreground">
          Failed to load sources. Is the controller running?
        </p>
      </div>
    );
  }

  if (sources.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">
          No sources registered. Import toolkits from GitHub to add a source.
        </p>
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>Repository</TableHead>
          <TableHead className="w-[100px]">Branch</TableHead>
          <TableHead className="w-[120px]">Last Synced</TableHead>
          <TableHead className="w-[100px]">Commit SHA</TableHead>
          <TableHead className="w-[80px]">Toolkits</TableHead>
          <TableHead className="w-[90px]">Status</TableHead>
          <TableHead className="w-[160px]" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {sources.map((source) => (
          <TableRow key={source.id}>
            <TableCell>
              <a
                href={source.github_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground hover:underline"
              >
                <GitBranch className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                {source.github_owner}/{source.github_repo}
                <ExternalLink className="h-3 w-3 text-muted-foreground" />
              </a>
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {source.branch}
              </span>
            </TableCell>
            <TableCell>
              <span className="text-xs text-muted-foreground">
                {formatRelativeTime(source.last_synced_at)}
              </span>
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {source.last_commit_sha
                  ? source.last_commit_sha.slice(0, 7)
                  : "--"}
              </span>
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {source.toolkit_count}
              </span>
            </TableCell>
            <TableCell>
              <Badge
                variant="secondary"
                className={
                  STATUS_COLORS[source.status] ||
                  "bg-gray-500/15 text-gray-400 border-gray-500/20"
                }
              >
                {source.status}
              </Badge>
            </TableCell>
            <TableCell>
              <div className="flex items-center gap-1">
                {onSync && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onSync(source.id)}
                    disabled={syncingId === source.id}
                    aria-label={`Check for updates for ${source.github_owner}/${source.github_repo}`}
                  >
                    {syncingId === source.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3.5 w-3.5" />
                    )}
                    <span className="ml-1 hidden lg:inline">
                      {syncingId === source.id ? "Syncing..." : "Check"}
                    </span>
                  </Button>
                )}
                {onDelete && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      if (
                        window.confirm(
                          `Delete source "${source.github_owner}/${source.github_repo}"? This will also remove all associated toolkits.`,
                        )
                      ) {
                        onDelete(source.id);
                      }
                    }}
                    aria-label={`Delete ${source.github_owner}/${source.github_repo}`}
                    className="text-muted-foreground hover:text-red-400"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

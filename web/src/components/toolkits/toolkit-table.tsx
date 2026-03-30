"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowUpDown,
  Loader2,
  Trash2,
  AlertTriangle,
  ShieldAlert,
} from "lucide-react";
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
import type { Toolkit } from "@/lib/types";
import { ToolkitType, ToolkitStatus, RiskLevel } from "@/lib/types";

type SortField = "name" | "type" | "version" | "usage_count" | "updated_at";
type SortDir = "asc" | "desc";

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

const TYPE_COLORS: Record<ToolkitType, string> = {
  [ToolkitType.SKILL]:
    "bg-purple-500/15 text-purple-400 border-purple-500/20",
  [ToolkitType.PLUGIN]:
    "bg-blue-500/15 text-blue-400 border-blue-500/20",
  [ToolkitType.PROFILE]:
    "bg-green-500/15 text-green-400 border-green-500/20",
  [ToolkitType.TOOL]:
    "bg-orange-500/15 text-orange-400 border-orange-500/20",
};

const STATUS_COLORS: Record<ToolkitStatus, string> = {
  [ToolkitStatus.AVAILABLE]:
    "bg-green-500/15 text-green-400 border-green-500/20",
  [ToolkitStatus.DISABLED]:
    "bg-gray-500/15 text-gray-400 border-gray-500/20",
  [ToolkitStatus.UPDATE_AVAILABLE]:
    "bg-yellow-500/15 text-yellow-400 border-yellow-500/20",
  [ToolkitStatus.ERROR]:
    "bg-red-500/15 text-red-400 border-red-500/20",
};

function RiskIndicator({ level }: { level: RiskLevel }) {
  if (level === RiskLevel.SAFE) return null;
  if (level === RiskLevel.MODERATE) {
    return (
      <AlertTriangle
        className="h-3.5 w-3.5 text-yellow-400"
        aria-label="Moderate risk"
      />
    );
  }
  return (
    <ShieldAlert
      className="h-3.5 w-3.5 text-red-400"
      aria-label="High risk"
    />
  );
}

interface ToolkitTableProps {
  toolkits: Toolkit[];
  isLoading: boolean;
  isError: boolean;
  searchFilter: string;
  typeFilter: string;
  statusFilter: string;
  onDelete?: (slug: string) => void;
}

export function ToolkitTable({
  toolkits,
  isLoading,
  isError,
  searchFilter,
  typeFilter,
  statusFilter,
  onDelete,
}: ToolkitTableProps) {
  const [sortField, setSortField] = useState<SortField>("updated_at");
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
    if (!toolkits) return [];
    let result = [...toolkits];

    if (searchFilter) {
      const lower = searchFilter.toLowerCase();
      result = result.filter(
        (t) =>
          t.name.toLowerCase().includes(lower) ||
          t.slug.toLowerCase().includes(lower) ||
          t.description.toLowerCase().includes(lower),
      );
    }

    if (typeFilter) {
      result = result.filter((t) => t.type === typeFilter);
    }

    if (statusFilter) {
      result = result.filter((t) => t.status === statusFilter);
    }

    result.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "type":
          cmp = a.type.localeCompare(b.type);
          break;
        case "version":
          cmp = a.version - b.version;
          break;
        case "usage_count":
          cmp = (a.usage_count || 0) - (b.usage_count || 0);
          break;
        case "updated_at":
          cmp = (a.updated_at || "").localeCompare(b.updated_at || "");
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });

    return result;
  }, [toolkits, searchFilter, typeFilter, statusFilter, sortField, sortDir]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">
          Loading toolkits...
        </span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-destructive-foreground">
          Failed to load toolkits. Is the controller running?
        </p>
      </div>
    );
  }

  if (filtered.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">
          {toolkits.length === 0
            ? "No toolkits registered. Import from GitHub to get started."
            : "No toolkits match the current filters."}
        </p>
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>
            <button
              onClick={() => toggleSort("name")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Name
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="w-[100px]">
            <button
              onClick={() => toggleSort("type")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Type
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="hidden md:table-cell w-[160px]">
            Source
          </TableHead>
          <TableHead className="w-[80px]">
            <button
              onClick={() => toggleSort("version")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Version
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="w-[100px]">Status</TableHead>
          <TableHead className="w-[60px]">Risk</TableHead>
          <TableHead className="w-[80px]">
            <button
              onClick={() => toggleSort("usage_count")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Usage
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="w-[60px]" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {filtered.map((toolkit) => (
          <TableRow key={toolkit.id || toolkit.slug}>
            <TableCell>
              <Link
                href={`/toolkits/${toolkit.slug}`}
                className="text-sm font-medium text-foreground hover:underline"
              >
                {toolkit.name}
              </Link>
              {toolkit.description && (
                <div className="text-xs text-muted-foreground mt-0.5 truncate max-w-[300px]">
                  {toolkit.description}
                </div>
              )}
            </TableCell>
            <TableCell>
              <Badge
                variant="secondary"
                className={TYPE_COLORS[toolkit.type]}
              >
                {toolkit.type}
              </Badge>
            </TableCell>
            <TableCell className="hidden md:table-cell">
              <span className="text-xs font-mono text-muted-foreground truncate max-w-[140px] block">
                {toolkit.source_id
                  ? toolkit.path.split("/").slice(0, 2).join("/")
                  : "--"}
              </span>
            </TableCell>
            <TableCell>
              <Badge variant="info">v{toolkit.version}</Badge>
            </TableCell>
            <TableCell>
              <Badge
                variant="secondary"
                className={STATUS_COLORS[toolkit.status]}
              >
                {toolkit.status.replace("_", " ")}
              </Badge>
            </TableCell>
            <TableCell>
              <RiskIndicator level={toolkit.risk_level} />
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {toolkit.usage_count ?? 0}
              </span>
            </TableCell>
            <TableCell>
              {onDelete && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.preventDefault();
                    if (
                      window.confirm(
                        `Delete toolkit "${toolkit.name}"? This action cannot be undone.`,
                      )
                    ) {
                      onDelete(toolkit.slug);
                    }
                  }}
                  aria-label={`Delete ${toolkit.name}`}
                >
                  <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive-foreground" />
                </Button>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

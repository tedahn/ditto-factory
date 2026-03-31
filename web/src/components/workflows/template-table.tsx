"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpDown, Loader2, Trash2, Play } from "lucide-react";
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
import type { WorkflowTemplate } from "@/lib/types";

type SortField = "name" | "slug" | "steps" | "updated_at";
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

function getStepCount(template: WorkflowTemplate): number {
  if (template.steps && Array.isArray(template.steps)) {
    return template.steps.length;
  }
  if (template.definition) {
    const def = template.definition as Record<string, unknown>;
    if (Array.isArray(def.steps)) return def.steps.length;
    if (Array.isArray(def.nodes)) return def.nodes.length;
  }
  return 0;
}

interface TemplateTableProps {
  templates: WorkflowTemplate[];
  isLoading: boolean;
  isError: boolean;
  searchFilter: string;
  onDelete?: (slug: string) => void;
}

export function TemplateTable({
  templates,
  isLoading,
  isError,
  searchFilter,
  onDelete,
}: TemplateTableProps) {
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
    if (!templates) return [];
    let result = [...templates];

    if (searchFilter) {
      const lower = searchFilter.toLowerCase();
      result = result.filter(
        (t) =>
          t.name.toLowerCase().includes(lower) ||
          t.slug.toLowerCase().includes(lower) ||
          (t.description || "").toLowerCase().includes(lower),
      );
    }

    result.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "slug":
          cmp = a.slug.localeCompare(b.slug);
          break;
        case "steps":
          cmp = getStepCount(a) - getStepCount(b);
          break;
        case "updated_at":
          cmp = (a.updated_at || "").localeCompare(b.updated_at || "");
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });

    return result;
  }, [templates, searchFilter, sortField, sortDir]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">
          Loading templates...
        </span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-destructive-foreground">
          Failed to load workflow templates. Is the controller running?
        </p>
      </div>
    );
  }

  if (filtered.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">
          {templates.length === 0
            ? "No workflow templates yet. Create one to get started."
            : "No templates match the current filter."}
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
          <TableHead className="w-[160px]">
            <button
              onClick={() => toggleSort("slug")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Slug
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="w-[100px]">
            <button
              onClick={() => toggleSort("steps")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Steps
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="w-[120px]">
            <button
              onClick={() => toggleSort("updated_at")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Updated
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="w-[100px]" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {filtered.map((template) => (
          <TableRow key={template.id || template.slug}>
            <TableCell>
              <Link
                href={`/workflows/${template.slug}/edit`}
                className="text-sm font-medium text-foreground hover:underline"
              >
                {template.name}
              </Link>
              {template.description && (
                <div className="text-xs text-muted-foreground mt-0.5 truncate max-w-[300px]">
                  {template.description}
                </div>
              )}
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {template.slug}
              </span>
            </TableCell>
            <TableCell>
              <Badge variant="info">{getStepCount(template)} steps</Badge>
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {formatRelativeTime(template.updated_at)}
              </span>
            </TableCell>
            <TableCell>
              <div className="flex items-center gap-1">
                <Link href={`/workflows/${template.slug}/run`}>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Run ${template.name}`}
                  >
                    <Play className="h-3.5 w-3.5 text-emerald-400" />
                  </Button>
                </Link>
                {onDelete && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={(e) => {
                      e.preventDefault();
                      if (
                        window.confirm(
                          `Delete template "${template.name}"? This action cannot be undone.`,
                        )
                      ) {
                        onDelete(template.slug);
                      }
                    }}
                    aria-label={`Delete ${template.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive-foreground" />
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

"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpDown, Loader2, Trash2 } from "lucide-react";
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
import type { Skill } from "@/lib/types";

type SortField = "name" | "slug" | "version" | "usage_count" | "updated_at";
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

interface SkillTableProps {
  skills: Skill[];
  isLoading: boolean;
  isError: boolean;
  searchFilter: string;
  tagFilter: string;
  onDelete?: (slug: string) => void;
}

export function SkillTable({
  skills,
  isLoading,
  isError,
  searchFilter,
  tagFilter,
  onDelete,
}: SkillTableProps) {
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
    if (!skills) return [];
    let result = [...skills];

    if (searchFilter) {
      const lower = searchFilter.toLowerCase();
      result = result.filter(
        (s) =>
          s.name.toLowerCase().includes(lower) ||
          s.slug.toLowerCase().includes(lower) ||
          s.description.toLowerCase().includes(lower),
      );
    }

    if (tagFilter) {
      const lower = tagFilter.toLowerCase();
      result = result.filter((s) =>
        s.tags.some((t) => t.toLowerCase().includes(lower)),
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
  }, [skills, searchFilter, tagFilter, sortField, sortDir]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">
          Loading skills...
        </span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-destructive-foreground">
          Failed to load skills. Is the controller running?
        </p>
      </div>
    );
  }

  if (filtered.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">
          {skills.length === 0
            ? "No skills yet. Create a skill to get started."
            : "No skills match the current filters."}
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
          <TableHead className="hidden md:table-cell">Tags</TableHead>
          <TableHead className="w-[80px]">
            <button
              onClick={() => toggleSort("usage_count")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Usage
              <ArrowUpDown className="h-3 w-3" />
            </button>
          </TableHead>
          <TableHead className="w-[100px]">
            <button
              onClick={() => toggleSort("version")}
              className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
            >
              Version
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
          <TableHead className="w-[60px]" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {filtered.map((skill) => (
          <TableRow key={skill.id || skill.slug}>
            <TableCell>
              <Link
                href={`/skills/${skill.slug}/edit`}
                className="text-sm font-medium text-foreground hover:underline"
              >
                {skill.name}
              </Link>
              {skill.description && (
                <div className="text-xs text-muted-foreground mt-0.5 truncate max-w-[300px]">
                  {skill.description}
                </div>
              )}
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {skill.slug}
              </span>
            </TableCell>
            <TableCell className="hidden md:table-cell">
              <div className="flex flex-wrap gap-1">
                {skill.tags.slice(0, 4).map((tag) => (
                  <Badge key={tag} variant="secondary">
                    {tag}
                  </Badge>
                ))}
                {skill.tags.length > 4 && (
                  <Badge variant="secondary">+{skill.tags.length - 4}</Badge>
                )}
              </div>
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {skill.usage_count ?? 0}
              </span>
            </TableCell>
            <TableCell>
              <Badge variant="info">v{skill.version}</Badge>
            </TableCell>
            <TableCell>
              <span className="text-xs font-mono text-muted-foreground">
                {formatRelativeTime(skill.updated_at)}
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
                        `Delete skill "${skill.name}"? This action cannot be undone.`,
                      )
                    ) {
                      onDelete(skill.slug);
                    }
                  }}
                  aria-label={`Delete ${skill.name}`}
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

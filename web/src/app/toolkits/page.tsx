"use client";

import { useState } from "react";
import Link from "next/link";
import { Search, Download } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ToolkitTable } from "@/components/toolkits/toolkit-table";
import { useToolkits, useDeleteToolkit } from "@/lib/hooks";
import { ToolkitType, ToolkitStatus } from "@/lib/types";

export default function ToolkitsPage() {
  const [searchFilter, setSearchFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const { data, isLoading, isError } = useToolkits(
    typeFilter || statusFilter
      ? {
          ...(typeFilter ? { type: typeFilter } : {}),
          ...(statusFilter ? { status: statusFilter } : {}),
        }
      : undefined,
  );
  const deleteToolkit = useDeleteToolkit();

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Page header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">Toolkits</h1>
            <p className="text-sm text-muted-foreground">
              Manage imported skills, plugins, profiles, and tools
            </p>
          </div>
          <Link href="/toolkits/import">
            <Button size="sm">
              <Download className="h-4 w-4 mr-1" />
              Import from GitHub
            </Button>
          </Link>
        </div>

        {/* Search and filters */}
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search toolkits by name or description..."
                value={searchFilter}
                onChange={(e) => setSearchFilter(e.target.value)}
                className="pl-9"
                aria-label="Search toolkits"
              />
            </div>

            {/* Type filter */}
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              aria-label="Filter by type"
            >
              <option value="">All types</option>
              {Object.values(ToolkitType).map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>

            {/* Status filter */}
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              {Object.values(ToolkitStatus).map((s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ))}
            </select>

            {data && (
              <span className="text-xs text-muted-foreground font-mono ml-auto">
                {data.total} total
              </span>
            )}
          </div>
        </div>

        {/* Table */}
        <Card>
          <CardContent className="p-0">
            <ToolkitTable
              toolkits={data?.toolkits || []}
              isLoading={isLoading}
              isError={isError}
              searchFilter={searchFilter}
              typeFilter={typeFilter}
              statusFilter={statusFilter}
              onDelete={(slug) => deleteToolkit.mutate(slug)}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

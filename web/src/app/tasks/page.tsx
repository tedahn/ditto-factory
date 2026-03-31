"use client";

import { useState } from "react";
import Link from "next/link";
import { Plus } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { TaskTable } from "@/components/tasks/task-table";
import { useThreads } from "@/lib/hooks";

export default function TasksPage() {
  const { data: threads, isLoading, isError } = useThreads();
  const [statusFilter, setStatusFilter] = useState("all");
  const [repoFilter, setRepoFilter] = useState("");

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Page header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">Tasks</h1>
            <p className="text-sm text-muted-foreground">
              Manage and monitor agent tasks
            </p>
          </div>
          <Link href="/tasks/new">
            <Button size="sm">
              <Plus className="h-4 w-4 mr-1" />
              New Task
            </Button>
          </Link>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3">
          <Select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="w-40"
            aria-label="Filter by status"
          >
            <option value="all">All statuses</option>
            <option value="idle">Idle</option>
            <option value="running">Running</option>
            <option value="queued">Queued</option>
          </Select>
          <Input
            placeholder="Filter by repository..."
            value={repoFilter}
            onChange={(e) => setRepoFilter(e.target.value)}
            className="max-w-xs"
            aria-label="Filter by repository"
          />
          {threads && (
            <span className="text-xs text-muted-foreground font-mono ml-auto">
              {threads.length} total
            </span>
          )}
        </div>

        {/* Table */}
        <Card>
          <CardContent className="p-0">
            <TaskTable
              threads={threads || []}
              isLoading={isLoading}
              isError={isError}
              statusFilter={statusFilter}
              repoFilter={repoFilter}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

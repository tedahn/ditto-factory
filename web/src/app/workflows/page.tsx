"use client";

import { useState } from "react";
import Link from "next/link";
import { Plus, Search, List } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { TemplateTable } from "@/components/workflows/template-table";
import { useWorkflowTemplates, useDeleteWorkflowTemplate } from "@/lib/hooks";

export default function WorkflowsPage() {
  const { data, isLoading, isError } = useWorkflowTemplates();
  const deleteTemplate = useDeleteWorkflowTemplate();
  const [searchFilter, setSearchFilter] = useState("");

  const templates = data?.templates || [];

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Page header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              Workflows
            </h1>
            <p className="text-sm text-muted-foreground">
              Manage workflow templates and monitor executions
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/workflows/executions">
              <Button variant="outline" size="sm">
                <List className="h-4 w-4 mr-1" />
                Executions
              </Button>
            </Link>
            <Link href="/workflows/new">
              <Button size="sm">
                <Plus className="h-4 w-4 mr-1" />
                New Template
              </Button>
            </Link>
          </div>
        </div>

        {/* Search */}
        <div className="flex items-center gap-3">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search templates by name, slug, or description..."
              value={searchFilter}
              onChange={(e) => setSearchFilter(e.target.value)}
              className="pl-9"
              aria-label="Search workflow templates"
            />
          </div>
          {data && (
            <span className="text-xs text-muted-foreground font-mono ml-auto">
              {data.total ?? templates.length} total
            </span>
          )}
        </div>

        {/* Table */}
        <Card>
          <CardContent className="p-0">
            <TemplateTable
              templates={templates}
              isLoading={isLoading}
              isError={isError}
              searchFilter={searchFilter}
              onDelete={(slug) => deleteTemplate.mutate(slug)}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

"use client";

import Link from "next/link";
import { ArrowLeft, Loader2 } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { useWorkflowExecutions } from "@/lib/hooks";

function getStatusVariant(status: string) {
  switch (status) {
    case "completed":
    case "success":
      return "success" as const;
    case "failed":
    case "error":
      return "destructive" as const;
    case "running":
      return "info" as const;
    case "cancelled":
    case "paused":
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

export default function WorkflowExecutionsPage() {
  const { data, isLoading, isError } = useWorkflowExecutions();
  const executions = data?.executions || [];

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              Workflow Executions
            </h1>
            <p className="text-sm text-muted-foreground">
              Monitor running and completed workflow executions
            </p>
          </div>
          <Link href="/workflows">
            <Button variant="outline" size="sm">
              <ArrowLeft className="h-4 w-4 mr-1" />
              Templates
            </Button>
          </Link>
        </div>

        <Card>
          <CardContent className="p-0">
            {isLoading ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                <span className="ml-2 text-sm text-muted-foreground">
                  Loading executions...
                </span>
              </div>
            ) : isError ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <p className="text-sm text-destructive-foreground">
                  Failed to load executions.
                </p>
              </div>
            ) : executions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <p className="text-sm text-muted-foreground">
                  No executions yet. Run a workflow to get started.
                </p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Execution ID</TableHead>
                    <TableHead>Template</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Steps</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead>Triggered By</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {executions.map((exec) => (
                    <TableRow key={exec.execution_id}>
                      <TableCell>
                        <Link
                          href={`/workflows/executions/${exec.execution_id}`}
                          className="text-sm font-mono text-foreground hover:underline"
                        >
                          {exec.execution_id.slice(0, 8)}...
                        </Link>
                      </TableCell>
                      <TableCell>
                        <span className="text-sm font-mono text-muted-foreground">
                          {exec.template_slug}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant={getStatusVariant(exec.status)}>
                          {exec.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <span className="text-xs font-mono text-muted-foreground">
                          {exec.steps?.length || 0}
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className="text-xs font-mono text-muted-foreground">
                          {formatRelativeTime(exec.started_at)}
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className="text-xs text-muted-foreground">
                          {exec.triggered_by || "--"}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

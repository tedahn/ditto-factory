"use client";

import { use } from "react";
import { Loader2, Ban } from "lucide-react";
import Link from "next/link";
import { Header } from "@/components/layout/header";
import { Button } from "@/components/ui/button";
import { ExecutionView } from "@/components/workflows/execution-view";
import {
  useWorkflowExecution,
  useCancelWorkflowExecution,
} from "@/lib/hooks";

export default function WorkflowExecutionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: execution, isLoading, isError } = useWorkflowExecution(id);
  const cancelExecution = useCancelWorkflowExecution();

  const isRunning =
    execution?.status === "running" || execution?.status === "pending";

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl mx-auto space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-lg font-semibold text-foreground">
                Execution Detail
              </h1>
              <p className="text-sm text-muted-foreground font-mono">{id}</p>
            </div>
            <div className="flex items-center gap-2">
              {isRunning && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => cancelExecution.mutate(id)}
                  disabled={cancelExecution.isPending}
                >
                  {cancelExecution.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin mr-1" />
                  ) : (
                    <Ban className="h-4 w-4 mr-1" />
                  )}
                  Cancel
                </Button>
              )}
              <Link href="/workflows">
                <Button variant="outline" size="sm">
                  Back to Workflows
                </Button>
              </Link>
            </div>
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              <span className="ml-2 text-sm text-muted-foreground">
                Loading execution...
              </span>
            </div>
          ) : isError ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <p className="text-sm text-destructive-foreground">
                Failed to load execution. It may not exist.
              </p>
            </div>
          ) : execution ? (
            <ExecutionView execution={execution} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

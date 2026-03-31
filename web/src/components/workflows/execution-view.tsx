"use client";

import { useMemo } from "react";
import { Loader2, CheckCircle2, XCircle, Clock, Pause, Ban } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { WorkflowExecution, WorkflowExecutionStep } from "@/lib/types";

function getStatusIcon(status: string) {
  switch (status) {
    case "completed":
    case "success":
      return <CheckCircle2 className="h-4 w-4 text-emerald-400" />;
    case "failed":
    case "error":
      return <XCircle className="h-4 w-4 text-red-400" />;
    case "running":
      return <Loader2 className="h-4 w-4 text-blue-400 animate-spin" />;
    case "pending":
    case "queued":
      return <Clock className="h-4 w-4 text-muted-foreground" />;
    case "cancelled":
      return <Ban className="h-4 w-4 text-amber-400" />;
    case "paused":
      return <Pause className="h-4 w-4 text-amber-400" />;
    default:
      return <Clock className="h-4 w-4 text-muted-foreground" />;
  }
}

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

function formatDuration(startedAt?: string | null, completedAt?: string | null): string {
  if (!startedAt) return "--";
  const start = new Date(startedAt).getTime();
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  const diffSec = Math.floor((end - start) / 1000);
  if (diffSec < 60) return `${diffSec}s`;
  const min = Math.floor(diffSec / 60);
  const sec = diffSec % 60;
  if (min < 60) return `${min}m ${sec}s`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${min % 60}m`;
}

function formatTimestamp(dateStr?: string | null): string {
  if (!dateStr) return "--";
  return new Date(dateStr).toLocaleString();
}

interface StepGroup {
  level: number;
  steps: WorkflowExecutionStep[];
}

function groupStepsByDependency(steps: WorkflowExecutionStep[]): StepGroup[] {
  if (!steps || steps.length === 0) return [];

  // Build dependency levels
  const stepMap = new Map<string, WorkflowExecutionStep>();
  steps.forEach((s) => stepMap.set(s.name, s));

  const levels = new Map<string, number>();

  function getLevel(stepName: string): number {
    if (levels.has(stepName)) return levels.get(stepName)!;
    const step = stepMap.get(stepName);
    if (!step || !step.depends_on || step.depends_on.length === 0) {
      levels.set(stepName, 0);
      return 0;
    }
    const maxDepLevel = Math.max(
      ...step.depends_on.map((dep) => getLevel(dep)),
    );
    const level = maxDepLevel + 1;
    levels.set(stepName, level);
    return level;
  }

  steps.forEach((s) => getLevel(s.name));

  // Group by level
  const groupMap = new Map<number, WorkflowExecutionStep[]>();
  steps.forEach((s) => {
    const level = levels.get(s.name) || 0;
    if (!groupMap.has(level)) groupMap.set(level, []);
    groupMap.get(level)!.push(s);
  });

  return Array.from(groupMap.entries())
    .sort(([a], [b]) => a - b)
    .map(([level, groupSteps]) => ({ level, steps: groupSteps }));
}

interface ExecutionViewProps {
  execution: WorkflowExecution;
}

export function ExecutionView({ execution }: ExecutionViewProps) {
  const stepGroups = useMemo(
    () => groupStepsByDependency(execution.steps || []),
    [execution.steps],
  );

  return (
    <div className="space-y-6">
      {/* Execution header info */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <p className="text-xs text-muted-foreground">Status</p>
          <Badge variant={getStatusVariant(execution.status)} className="mt-1">
            {execution.status}
          </Badge>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Template</p>
          <p className="text-sm font-mono mt-1">{execution.template_slug}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Started</p>
          <p className="text-sm font-mono mt-1">
            {formatTimestamp(execution.started_at)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Duration</p>
          <p className="text-sm font-mono mt-1">
            {formatDuration(execution.started_at, execution.completed_at)}
          </p>
        </div>
      </div>

      {/* Parameters */}
      {execution.parameters &&
        Object.keys(execution.parameters).length > 0 && (
          <Card>
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground mb-2">Parameters</p>
              <pre className="text-xs font-mono text-foreground bg-secondary/50 rounded p-3 overflow-x-auto">
                {JSON.stringify(execution.parameters, null, 2)}
              </pre>
            </CardContent>
          </Card>
        )}

      {/* Step timeline */}
      <div className="space-y-4">
        <h3 className="text-sm font-medium text-foreground">
          Steps ({execution.steps?.length || 0})
        </h3>

        {stepGroups.length === 0 ? (
          <p className="text-sm text-muted-foreground">No steps available.</p>
        ) : (
          <div className="space-y-3">
            {stepGroups.map((group, groupIdx) => (
              <div key={group.level} className="relative">
                {/* Connector line */}
                {groupIdx > 0 && (
                  <div className="absolute left-1/2 -top-3 w-px h-3 bg-border" />
                )}

                {/* Steps in this group - parallel steps side by side */}
                <div
                  className={
                    group.steps.length > 1
                      ? "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"
                      : ""
                  }
                >
                  {group.steps.map((step) => (
                    <Card
                      key={step.name}
                      className={`border-l-2 ${
                        step.status === "running"
                          ? "border-l-blue-400"
                          : step.status === "completed" || step.status === "success"
                            ? "border-l-emerald-400"
                            : step.status === "failed" || step.status === "error"
                              ? "border-l-red-400"
                              : "border-l-border"
                      }`}
                    >
                      <CardContent className="p-4 space-y-2">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            {getStatusIcon(step.status)}
                            <span className="text-sm font-medium">
                              {step.name}
                            </span>
                          </div>
                          <Badge variant={getStatusVariant(step.status)}>
                            {step.status}
                          </Badge>
                        </div>

                        <div className="flex items-center gap-4 text-xs text-muted-foreground">
                          {step.agent_type && (
                            <span>
                              Agent:{" "}
                              <span className="font-mono">
                                {step.agent_type}
                              </span>
                            </span>
                          )}
                          <span>
                            Duration:{" "}
                            {formatDuration(step.started_at, step.completed_at)}
                          </span>
                        </div>

                        {step.started_at && (
                          <div className="text-xs text-muted-foreground">
                            Started: {formatTimestamp(step.started_at)}
                            {step.completed_at && (
                              <>
                                {" "}
                                | Completed: {formatTimestamp(step.completed_at)}
                              </>
                            )}
                          </div>
                        )}

                        {step.result_summary && (
                          <div className="text-xs text-muted-foreground bg-secondary/50 rounded p-2 mt-2">
                            {step.result_summary}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

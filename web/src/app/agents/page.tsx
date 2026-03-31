"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Header } from "@/components/layout/header";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AgentList } from "@/components/agents/agent-list";
import { AgentTypesTab } from "@/components/agents/agent-types-tab";
import { useThreads, useDashboardSummary, useWorkflowExecutions, useAgentPods } from "@/lib/hooks";

export default function AgentsPage() {
  return (
    <Suspense fallback={<div className="flex flex-col h-full -m-6"><Header /><div className="flex-1 p-6"><p className="text-sm text-muted-foreground">Loading...</p></div></div>}>
      <AgentsPageContent />
    </Suspense>
  );
}

function AgentsPageContent() {
  const searchParams = useSearchParams();
  const tab = searchParams.get("tab") || "threads";
  const { data: threads, isLoading, isError } = useThreads();
  const { data: summary } = useDashboardSummary();
  const { data: wfData } = useWorkflowExecutions();
  const { data: podsData } = useAgentPods();

  const activeThreads = (threads || []).filter(
    (t) => t.status === "running" || t.status === "queued",
  );

  const workflowExecutions = wfData?.executions || [];
  const pods = podsData?.pods || [];

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Page header */}
        <div>
          <h1 className="text-lg font-semibold text-foreground">Agents</h1>
          <p className="text-sm text-muted-foreground">
            Monitor active agents and their real-time status
          </p>
        </div>

        <Tabs defaultValue={tab} className="space-y-4">
          <TabsList>
            <TabsTrigger value="threads">Threads</TabsTrigger>
            <TabsTrigger value="types">Agent Types</TabsTrigger>
          </TabsList>

          <TabsContent value="threads" className="space-y-6">
            {/* Summary stats */}
            {summary && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatCard label="Active" value={summary.active_count} accent="emerald" />
                <StatCard label="Completed (24h)" value={summary.completed_24h} accent="blue" />
                <StatCard label="Failed (24h)" value={summary.failed_24h} accent="red" />
                <StatCard
                  label="Avg Duration"
                  value={formatDuration(summary.avg_duration_seconds)}
                  accent="amber"
                />
              </div>
            )}

            {/* Agent cards */}
            <AgentList threads={activeThreads} isLoading={isLoading} isError={isError} />

            {/* All threads (including inactive) */}
            {threads && threads.length > activeThreads.length && (
              <div className="space-y-3">
                <h2 className="text-sm font-medium text-muted-foreground">
                  Recent Idle Threads
                </h2>
                <AgentList
                  threads={threads.filter((t) => t.status === "idle").slice(0, 6)}
                  isLoading={false}
                  isError={false}
                />
              </div>
            )}
          </TabsContent>

          {/* K8s Agent Pods */}
          {pods.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-sm font-medium text-muted-foreground">
                Agent Pods ({pods.length})
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {pods.map((pod) => (
                  <Card key={pod.name}>
                    <CardContent className="p-4 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-foreground font-mono">
                          {pod.name}
                        </span>
                        <span className={`text-xs px-2 py-0.5 rounded-full ${
                          pod.status === "running" ? "bg-emerald-500/15 text-emerald-400" :
                          pod.status === "completed" ? "bg-blue-500/15 text-blue-400" :
                          pod.status === "failed" ? "bg-red-500/15 text-red-400" :
                          pod.status === "ContainerCreating" ? "bg-amber-500/15 text-amber-400" :
                          "bg-gray-500/15 text-gray-400"
                        }`}>
                          {pod.status}
                        </span>
                      </div>
                      {pod.thread_id && (
                        <p className="text-xs text-muted-foreground font-mono">
                          Thread: {pod.thread_id.slice(0, 12)}
                        </p>
                      )}
                      <p className="text-xs text-muted-foreground">
                        {pod.started_at ? new Date(pod.started_at).toLocaleTimeString() : "--"}
                        {pod.node && ` on ${pod.node}`}
                      </p>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Workflow Agents */}
          {workflowExecutions.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-sm font-medium text-muted-foreground">
                Workflow Agents ({workflowExecutions.length})
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                {workflowExecutions.map((exec: any) => {
                  const execId = String(exec.execution_id || exec.id || "");
                  const execStatus = String(exec.status || "unknown");
                  const execStarted = (exec.started_at || null) as string | null;
                  return (
                  <Card key={execId}>
                    <CardContent className="p-4 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-foreground font-mono">
                          {execId.slice(0, 8)}
                        </span>
                        <span className={`text-xs px-2 py-0.5 rounded-full ${
                          execStatus === "running" ? "bg-emerald-500/15 text-emerald-400" :
                          execStatus === "completed" ? "bg-blue-500/15 text-blue-400" :
                          execStatus === "failed" ? "bg-red-500/15 text-red-400" :
                          "bg-gray-500/15 text-gray-400"
                        }`}>
                          {execStatus}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Started: {execStarted ? new Date(execStarted).toLocaleTimeString() : "--"}
                      </p>
                    </CardContent>
                  </Card>
                  );
                })}
              </div>
            </div>
          )}

          <TabsContent value="types">
            <AgentTypesTab />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | string;
  accent: string;
}) {
  const colorMap: Record<string, string> = {
    emerald: "text-emerald-400",
    blue: "text-blue-400",
    red: "text-red-400",
    amber: "text-amber-400",
  };

  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={`text-2xl font-semibold font-mono mt-1 ${colorMap[accent] || "text-foreground"}`}>
          {value}
        </p>
      </CardContent>
    </Card>
  );
}

function formatDuration(seconds: number): string {
  if (seconds === 0) return "--";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

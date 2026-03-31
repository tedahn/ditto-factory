"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Header } from "@/components/layout/header";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AgentList } from "@/components/agents/agent-list";
import { AgentTypesTab } from "@/components/agents/agent-types-tab";
import { useThreads, useDashboardSummary } from "@/lib/hooks";

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

  const activeThreads = (threads || []).filter(
    (t) => t.status === "running" || t.status === "queued",
  );

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

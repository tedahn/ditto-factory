"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import { ArrowLeft, Clock, Server, Cpu, Layers, Terminal } from "lucide-react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useTaskDetail, useJobs } from "@/lib/hooks";
import { useEventSource, type SSEEvent } from "@/lib/sse";

function ElapsedTimer({ since }: { since: string | null | undefined }) {
  const [elapsed, setElapsed] = useState("--");

  useEffect(() => {
    if (!since) return;
    function update() {
      const diff = Math.max(0, Math.floor((Date.now() - new Date(since!).getTime()) / 1000));
      const h = Math.floor(diff / 3600);
      const m = Math.floor((diff % 3600) / 60);
      const s = diff % 60;
      setElapsed(h > 0 ? `${h}h ${m}m ${s}s` : m > 0 ? `${m}m ${s}s` : `${s}s`);
    }
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, [since]);

  return <span className="font-mono">{elapsed}</span>;
}

interface AgentDetailProps {
  threadId: string;
}

export function AgentDetail({ threadId }: AgentDetailProps) {
  const { data: task, isLoading: taskLoading } = useTaskDetail(threadId);
  const { data: jobs } = useJobs(threadId);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const sseUrl = `/api/proxy/api/events/${threadId}`;
  const { events, status: sseStatus } = useEventSource(sseUrl, {
    enabled: task?.status === "running",
  });

  const latestJob = useMemo(() => {
    if (!jobs || jobs.length === 0) return null;
    return jobs[jobs.length - 1];
  }, [jobs]);

  // Log lines extracted from SSE events
  const logLines = useMemo(() => {
    return events
      .filter((e) => e.event === "log_line" || e.event === "message")
      .map((e) => {
        try {
          const parsed = JSON.parse(e.data);
          return {
            timestamp: new Date(e.timestamp).toLocaleTimeString(),
            text: parsed.line || parsed.message || e.data,
          };
        } catch {
          return {
            timestamp: new Date(e.timestamp).toLocaleTimeString(),
            text: e.data,
          };
        }
      });
  }, [events]);

  // Result artifacts from SSE
  const resultEvents = useMemo(() => {
    return events
      .filter((e) => e.event === "result")
      .map((e) => {
        try {
          return JSON.parse(e.data);
        } catch {
          return null;
        }
      })
      .filter(Boolean);
  }, [events]);

  // Auto-scroll log container
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logLines, autoScroll]);

  // Detect manual scroll to disable auto-scroll
  function handleScroll() {
    if (!logContainerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = logContainerRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 40;
    setAutoScroll(isAtBottom);
  }

  if (taskLoading) {
    return (
      <div className="space-y-4 animate-pulse">
        <div className="h-6 w-48 bg-muted rounded" />
        <div className="grid grid-cols-5 gap-4">
          <div className="col-span-2 h-64 bg-muted rounded" />
          <div className="col-span-3 h-64 bg-muted rounded" />
        </div>
      </div>
    );
  }

  if (!task) {
    return (
      <Card>
        <CardContent className="p-8 text-center">
          <p className="text-sm text-muted-foreground">Agent not found</p>
          <Link href="/agents">
            <Button variant="outline" size="sm" className="mt-3">
              Back to Agents
            </Button>
          </Link>
        </CardContent>
      </Card>
    );
  }

  const statusVariant =
    task.status === "running"
      ? "success"
      : task.status === "completed"
        ? "info"
        : task.status === "failed"
          ? "destructive"
          : "secondary";

  return (
    <div className="space-y-4">
      {/* Breadcrumb */}
      <div className="flex items-center gap-3">
        <Link href="/agents">
          <Button variant="ghost" size="sm" className="gap-1.5">
            <ArrowLeft className="h-3.5 w-3.5" />
            Agents
          </Button>
        </Link>
        <span className="text-muted-foreground">/</span>
        <span className="text-sm font-mono text-muted-foreground">
          {threadId.slice(0, 12)}...
        </span>
        <Badge variant={statusVariant}>{task.status}</Badge>
        {sseStatus === "connected" && (
          <Badge variant="success" className="text-[10px]">
            LIVE
          </Badge>
        )}
      </div>

      {/* Split view */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4 h-[calc(100vh-12rem)]">
        {/* Left panel: Agent info (40%) */}
        <Card className="lg:col-span-2 overflow-y-auto">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="h-4 w-4" />
              Agent Info
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <InfoRow label="Thread ID" value={threadId} mono />
            {task.repo_owner && task.repo_name && (
              <InfoRow
                label="Repository"
                value={`${task.repo_owner}/${task.repo_name}`}
              />
            )}
            <InfoRow label="Status" value={task.status} />
            {task.current_job_name && (
              <InfoRow label="Job Name" value={task.current_job_name} mono />
            )}
            {latestJob && (
              <>
                <InfoRow label="Agent Type" value={latestJob.agent_type} />
                {latestJob.skills_injected.length > 0 && (
                  <div>
                    <span className="text-xs text-muted-foreground">Skills Injected</span>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {latestJob.skills_injected.map((skill) => (
                        <Badge key={skill} variant="secondary" className="text-[10px]">
                          {skill}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                {latestJob.started_at && (
                  <InfoRow label="Started At" value={new Date(latestJob.started_at).toLocaleString()} />
                )}
              </>
            )}
            {task.created_at && (
              <div>
                <span className="text-xs text-muted-foreground">Elapsed</span>
                <div className="text-sm mt-0.5">
                  <Clock className="inline h-3 w-3 mr-1 text-muted-foreground" />
                  <ElapsedTimer since={task.created_at} />
                </div>
              </div>
            )}

            {/* Result artifacts */}
            {resultEvents.length > 0 && (
              <div className="pt-3 border-t border-border">
                <span className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                  <Layers className="h-3 w-3" />
                  Result Artifacts
                </span>
                <div className="mt-2 space-y-2">
                  {resultEvents.map((result, i) => (
                    <Card key={i} className="bg-muted/30">
                      <CardContent className="p-3 text-xs">
                        {result.pr_url && (
                          <a
                            href={result.pr_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-400 hover:underline"
                          >
                            {result.pr_url}
                          </a>
                        )}
                        {result.branch && (
                          <p className="font-mono text-muted-foreground mt-1">
                            branch: {result.branch}
                          </p>
                        )}
                        {result.commit_count !== undefined && (
                          <p className="text-muted-foreground mt-0.5">
                            {result.commit_count} commit(s)
                          </p>
                        )}
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Right panel: Log stream (60%) */}
        <Card className="lg:col-span-3 flex flex-col overflow-hidden">
          <CardHeader className="shrink-0 pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Terminal className="h-4 w-4" />
                Live Log Stream
              </CardTitle>
              <div className="flex items-center gap-2">
                {!autoScroll && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-xs h-6"
                    onClick={() => {
                      setAutoScroll(true);
                      if (logContainerRef.current) {
                        logContainerRef.current.scrollTop =
                          logContainerRef.current.scrollHeight;
                      }
                    }}
                  >
                    Resume auto-scroll
                  </Button>
                )}
                <span className="text-[10px] font-mono text-muted-foreground">
                  {logLines.length} lines
                </span>
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex-1 p-0 overflow-hidden">
            <div
              ref={logContainerRef}
              onScroll={handleScroll}
              className="h-full overflow-y-auto bg-[#0d1117] p-4 font-mono text-xs leading-5"
              role="log"
              aria-live="polite"
              aria-label="Agent log stream"
            >
              {logLines.length === 0 ? (
                <div className="flex items-center justify-center h-full text-muted-foreground">
                  {task.status === "running" ? (
                    <span className="animate-pulse">Waiting for log output...</span>
                  ) : (
                    <span>No log data available</span>
                  )}
                </div>
              ) : (
                logLines.map((line, i) => (
                  <div key={i} className="flex gap-3 hover:bg-white/5 px-1 -mx-1 rounded">
                    <span className="text-muted-foreground shrink-0 select-none w-16 text-right">
                      {line.timestamp}
                    </span>
                    <span className="text-emerald-400 whitespace-pre-wrap break-all">
                      {line.text}
                    </span>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function InfoRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <span className="text-xs text-muted-foreground">{label}</span>
      <p className={`text-sm mt-0.5 truncate ${mono ? "font-mono" : ""}`}>{value}</p>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Bot, Clock, GitBranch } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Thread } from "@/lib/types";

function StatusDot({ status }: { status: string }) {
  if (status === "running") {
    return (
      <span className="relative flex h-2.5 w-2.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
        <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
      </span>
    );
  }
  if (status === "queued") {
    return <span className="h-2.5 w-2.5 rounded-full bg-amber-500" />;
  }
  return <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground" />;
}

function ElapsedTime({ since }: { since: string | null | undefined }) {
  const [elapsed, setElapsed] = useState("");

  useEffect(() => {
    if (!since) {
      setElapsed("--");
      return;
    }

    function update() {
      const start = new Date(since!).getTime();
      const now = Date.now();
      const diff = Math.max(0, Math.floor((now - start) / 1000));
      const h = Math.floor(diff / 3600);
      const m = Math.floor((diff % 3600) / 60);
      const s = diff % 60;
      if (h > 0) {
        setElapsed(`${h}h ${m}m ${s}s`);
      } else if (m > 0) {
        setElapsed(`${m}m ${s}s`);
      } else {
        setElapsed(`${s}s`);
      }
    }

    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [since]);

  return <span className="font-mono text-xs text-muted-foreground">{elapsed}</span>;
}

interface AgentListProps {
  threads: Thread[];
  isLoading: boolean;
  isError: boolean;
}

export function AgentList({ threads, isLoading, isError }: AgentListProps) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i} className="animate-pulse">
            <CardHeader className="pb-3">
              <div className="h-4 w-32 bg-muted rounded" />
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                <div className="h-3 w-48 bg-muted rounded" />
                <div className="h-3 w-24 bg-muted rounded" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardContent className="p-8 text-center">
          <p className="text-sm text-red-400">Failed to load agents. Please try again.</p>
        </CardContent>
      </Card>
    );
  }

  if (threads.length === 0) {
    return (
      <Card>
        <CardContent className="p-12 text-center">
          <Bot className="h-10 w-10 text-muted-foreground mx-auto mb-3 opacity-40" />
          <p className="text-sm text-muted-foreground">No active agents</p>
          <p className="text-xs text-muted-foreground mt-1">
            Agents will appear here when tasks are running
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {threads.map((thread) => (
        <Link key={thread.id} href={`/agents/${thread.id}`}>
          <Card className="hover:border-primary/30 transition-colors cursor-pointer">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <StatusDot status={thread.status} />
                  <CardTitle className="text-sm truncate">
                    {thread.repo_owner}/{thread.repo_name}
                  </CardTitle>
                </div>
                <Badge
                  variant={thread.status === "running" ? "success" : "warning"}
                >
                  {thread.status}
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {thread.current_job_name && (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <GitBranch className="h-3 w-3" />
                    <span className="font-mono truncate">{thread.current_job_name}</span>
                  </div>
                )}
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  <ElapsedTime since={thread.created_at} />
                </div>
                <div className="flex items-center gap-1.5">
                  <Badge variant="secondary" className="text-[10px]">
                    {thread.source}
                  </Badge>
                  <span className="text-[10px] font-mono text-muted-foreground truncate">
                    {thread.id.slice(0, 8)}...
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>
        </Link>
      ))}
    </div>
  );
}

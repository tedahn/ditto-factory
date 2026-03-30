"use client";

import { useHealth } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export function Header() {
  const { data, isError, isLoading } = useHealth();
  const isHealthy = data?.status === "ok";

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-card px-6">
      <h1 className="text-sm font-medium text-foreground">Dashboard</h1>
      <div className="flex items-center gap-2">
        <div
          className={cn(
            "h-2 w-2 rounded-full",
            isLoading
              ? "bg-muted-foreground animate-pulse"
              : isHealthy
                ? "bg-emerald-500"
                : "bg-red-500",
          )}
          aria-label={
            isLoading
              ? "Checking system health"
              : isHealthy
                ? "System healthy"
                : "System unhealthy"
          }
        />
        <span className="text-xs text-muted-foreground font-mono">
          {isLoading
            ? "checking..."
            : isError
              ? "offline"
              : isHealthy
                ? "healthy"
                : "degraded"}
        </span>
      </div>
    </header>
  );
}

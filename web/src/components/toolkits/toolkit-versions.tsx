"use client";

import { Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ToolkitVersion } from "@/lib/types";

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "--";
  return new Date(dateStr).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface ToolkitVersionsProps {
  versions: ToolkitVersion[];
  currentVersion: number;
  isLoading: boolean;
  isRollingBack: boolean;
  onRollback: (version: number) => void;
}

export function ToolkitVersions({
  versions,
  currentVersion,
  isLoading,
  isRollingBack,
  onRollback,
}: ToolkitVersionsProps) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">
          Loading version history...
        </span>
      </div>
    );
  }

  if (!versions || versions.length === 0) {
    return (
      <div className="py-8 text-center">
        <p className="text-sm text-muted-foreground">No version history available.</p>
      </div>
    );
  }

  const sorted = [...versions].sort((a, b) => b.version - a.version);

  return (
    <div className="space-y-0">
      {sorted.map((v, idx) => {
        const isCurrent = v.version === currentVersion;
        const isLast = idx === sorted.length - 1;

        return (
          <div key={v.id || v.version} className="flex gap-4">
            {/* Timeline connector */}
            <div className="flex flex-col items-center">
              <div
                className={`mt-1 h-3 w-3 rounded-full border-2 shrink-0 ${
                  isCurrent
                    ? "bg-primary border-primary"
                    : "bg-transparent border-muted-foreground/40"
                }`}
              />
              {!isLast && (
                <div className="w-px flex-1 bg-border" />
              )}
            </div>

            {/* Version content */}
            <div className={`pb-6 flex-1 ${isLast ? "pb-0" : ""}`}>
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-2">
                  <span
                    className={`text-sm font-semibold ${
                      isCurrent ? "text-primary" : "text-foreground"
                    }`}
                  >
                    v{v.version}
                  </span>
                  {isCurrent && (
                    <span className="text-[10px] uppercase tracking-wider text-primary font-medium px-1.5 py-0.5 rounded bg-primary/10">
                      current
                    </span>
                  )}
                </div>

                {!isCurrent && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onRollback(v.version)}
                    disabled={isRollingBack}
                    className="text-xs h-7"
                  >
                    {isRollingBack ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <RotateCcw className="h-3 w-3" />
                    )}
                    Rollback
                  </Button>
                )}
              </div>

              <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                <span>{formatDate(v.created_at)}</span>
                <span className="font-mono">{v.pinned_sha.slice(0, 7)}</span>
              </div>

              {v.changelog && (
                <p className="mt-1.5 text-sm text-muted-foreground leading-relaxed">
                  {v.changelog}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

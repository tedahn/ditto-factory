"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, RotateCcw, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useSkillVersions, useRollbackSkill } from "@/lib/hooks";

function formatDateTime(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface VersionHistoryProps {
  slug: string;
  currentVersion: number;
}

export function VersionHistory({ slug, currentVersion }: VersionHistoryProps) {
  const [expanded, setExpanded] = useState(false);
  const { data: versions, isLoading } = useSkillVersions(slug);
  const rollback = useRollbackSkill(slug);

  const handleRollback = (version: number) => {
    if (
      window.confirm(
        `Rollback to version ${version}? This will create a new version with the content from v${version}.`,
      )
    ) {
      rollback.mutate(version);
    }
  };

  return (
    <Card>
      <CardHeader
        className="cursor-pointer select-none"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            {expanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            Version History
          </CardTitle>
          <Badge variant="info">Current: v{currentVersion}</Badge>
        </div>
      </CardHeader>

      {expanded && (
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              <span className="ml-2 text-sm text-muted-foreground">
                Loading versions...
              </span>
            </div>
          ) : !versions || versions.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4">
              No version history available.
            </p>
          ) : (
            <div className="space-y-0">
              {versions.map((v) => (
                <div
                  key={v.version}
                  className="flex items-start justify-between py-3 border-b border-border last:border-0"
                >
                  <div className="flex items-start gap-3 min-w-0">
                    <div className="flex flex-col items-center pt-0.5">
                      <div className="h-2 w-2 rounded-full bg-primary" />
                      <div className="w-px h-full bg-border" />
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Badge
                          variant={
                            v.version === currentVersion
                              ? "success"
                              : "secondary"
                          }
                        >
                          v{v.version}
                        </Badge>
                        {v.version === currentVersion && (
                          <span className="text-xs text-emerald-400">
                            current
                          </span>
                        )}
                      </div>
                      {v.changelog && (
                        <p className="text-sm text-muted-foreground mt-1">
                          {v.changelog}
                        </p>
                      )}
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs font-mono text-muted-foreground">
                          {v.created_by}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {formatDateTime(v.created_at)}
                        </span>
                      </div>
                    </div>
                  </div>

                  {v.version !== currentVersion && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRollback(v.version)}
                      disabled={rollback.isPending}
                      aria-label={`Rollback to version ${v.version}`}
                      className="shrink-0 ml-2"
                    >
                      {rollback.isPending ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RotateCcw className="h-3.5 w-3.5" />
                      )}
                      <span className="ml-1 text-xs">Rollback</span>
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}

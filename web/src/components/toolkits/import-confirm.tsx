"use client";

import { CheckCircle2, XCircle, Loader2 } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";

interface ImportConfirmProps {
  componentNames: string[];
  isLoading: boolean;
  isSuccess: boolean;
  isError: boolean;
  error: string | null;
  importedCount: number | null;
  onRetry: () => void;
}

export function ImportConfirm({
  componentNames,
  isLoading,
  isSuccess,
  isError,
  error,
  importedCount,
  onRetry,
}: ImportConfirmProps) {
  return (
    <div className="space-y-6">
      {/* Components being imported */}
      <div className="rounded-lg border border-border bg-card p-4 space-y-3">
        <h3 className="text-sm font-medium text-foreground">
          {isLoading
            ? "Importing..."
            : isSuccess
              ? "Import complete"
              : isError
                ? "Import failed"
                : "Components to import"}
        </h3>

        <div className="space-y-2">
          {componentNames.map((name) => (
            <div
              key={name}
              className="flex items-center gap-3 text-sm py-1"
            >
              {isLoading ? (
                <Loader2 className="h-4 w-4 text-muted-foreground animate-spin shrink-0" />
              ) : isSuccess ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
              ) : isError ? (
                <XCircle className="h-4 w-4 text-red-400 shrink-0" />
              ) : (
                <div className="h-4 w-4 rounded-full border border-muted-foreground/40 shrink-0" />
              )}
              <span className="text-foreground">{name}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Importing {componentNames.length} component{componentNames.length !== 1 ? "s" : ""}...
        </div>
      )}

      {/* Success */}
      {isSuccess && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-emerald-400" />
            <p className="text-sm font-medium text-emerald-400">
              Successfully imported {importedCount ?? componentNames.length} component
              {(importedCount ?? componentNames.length) !== 1 ? "s" : ""}
            </p>
          </div>
          <Link href="/toolkits">
            <Button variant="outline" size="sm">
              View Toolkits
            </Button>
          </Link>
        </div>
      )}

      {/* Error */}
      {isError && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <XCircle className="h-5 w-5 text-red-400" />
            <p className="text-sm font-medium text-red-400">Import failed</p>
          </div>
          {error && (
            <p className="text-sm text-red-300/80">{error}</p>
          )}
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry Import
          </Button>
        </div>
      )}
    </div>
  );
}

"use client";

import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ToolkitUpdateProps {
  slug: string;
  isApplying: boolean;
  onApply: () => void;
}

export function ToolkitUpdate({ slug, isApplying, onApply }: ToolkitUpdateProps) {
  return (
    <div
      className="flex items-center justify-between gap-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3"
      role="alert"
    >
      <div className="flex items-center gap-3">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
        <p className="text-sm text-amber-300">
          Update available — source repository has new commits
        </p>
      </div>
      <Button
        size="sm"
        onClick={onApply}
        disabled={isApplying}
        className="shrink-0 bg-amber-600 text-white hover:bg-amber-500"
      >
        {isApplying ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Applying...
          </>
        ) : (
          <>
            <RefreshCw className="h-3.5 w-3.5" />
            Apply Update
          </>
        )}
      </Button>
    </div>
  );
}

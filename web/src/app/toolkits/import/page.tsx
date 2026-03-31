"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { X, Package, CheckCircle2, AlertCircle } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Button } from "@/components/ui/button";
import { ImportUrlInput } from "@/components/toolkits/import-url-input";
import { useStartOnboarding } from "@/lib/hooks";
import { useRouter } from "next/navigation";

interface ImportResult {
  toolkitSlug: string;
  toolkitName?: string;
  category?: string;
  componentCount?: number;
}

export default function ImportPage() {
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const router = useRouter();
  const onboard = useStartOnboarding();

  const handleImport = useCallback(
    async (url: string, branch: string) => {
      setError(null);
      setResult(null);
      try {
        const data = await onboard.mutateAsync({ github_url: url, branch });
        if (
          data.status === "completed" &&
          data.result?.toolkit_slug &&
          typeof data.result.toolkit_slug === "string"
        ) {
          setResult({
            toolkitSlug: data.result.toolkit_slug,
            toolkitName: typeof data.result.toolkit_name === "string" ? data.result.toolkit_name : data.result.toolkit_slug,
            category: typeof data.result.category === "string" ? data.result.category : undefined,
            componentCount: typeof data.result.component_count === "number" ? data.result.component_count : undefined,
          });
        } else if (data.error) {
          setError(typeof data.error === "string" ? data.error : "Import failed. Please try again.");
        } else {
          setError("Import completed but no toolkit was created. Please try again.");
        }
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Import failed. Please try again.",
        );
      }
    },
    [onboard],
  );

  const handleTryAgain = useCallback(() => {
    setResult(null);
    setError(null);
    onboard.reset();
  }, [onboard]);

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Top bar */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              Import from GitHub
            </h1>
            <p className="text-sm text-muted-foreground">
              Import a toolkit from a GitHub repository
            </p>
          </div>
          <Link href="/toolkits">
            <Button variant="ghost" size="sm">
              <X className="h-4 w-4 mr-1" />
              Cancel
            </Button>
          </Link>
        </div>

        {/* Content */}
        <div className="max-w-2xl">
          {/* Success state */}
          {result && (
            <div className="space-y-6">
              <div className="rounded-lg border border-green-500/30 bg-green-500/10 p-6 space-y-4">
                <div className="flex items-start gap-3">
                  <CheckCircle2 className="h-5 w-5 text-green-500 mt-0.5 shrink-0" />
                  <div className="space-y-1">
                    <h2 className="text-base font-semibold text-foreground">
                      Toolkit imported successfully
                    </h2>
                    <p className="text-sm text-muted-foreground">
                      The repository has been analyzed and imported.
                    </p>
                  </div>
                </div>
                <div className="ml-8 space-y-2">
                  <div className="flex items-center gap-2 text-sm">
                    <Package className="h-4 w-4 text-muted-foreground" />
                    <span className="font-medium text-foreground">{result.toolkitName}</span>
                  </div>
                  {result.category && (
                    <p className="text-sm text-muted-foreground">
                      Category: <span className="text-foreground">{result.category}</span>
                    </p>
                  )}
                  {result.componentCount != null && (
                    <p className="text-sm text-muted-foreground">
                      Components: <span className="text-foreground">{result.componentCount}</span>
                    </p>
                  )}
                </div>
              </div>
              <div className="flex gap-3">
                <Button
                  onClick={() => router.push(`/toolkits/${result.toolkitSlug}`)}
                  size="lg"
                >
                  View Toolkit
                </Button>
                <Button variant="outline" size="lg" onClick={handleTryAgain}>
                  Import Another
                </Button>
              </div>
            </div>
          )}

          {/* Error state (after a failed import, not inline validation) */}
          {!result && error && !onboard.isPending && (
            <div className="space-y-6">
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-6">
                <div className="flex items-start gap-3">
                  <AlertCircle className="h-5 w-5 text-red-500 mt-0.5 shrink-0" />
                  <div className="space-y-1">
                    <h2 className="text-base font-semibold text-foreground">
                      Import failed
                    </h2>
                    <p className="text-sm text-red-400">{error}</p>
                  </div>
                </div>
              </div>
              <Button variant="outline" size="lg" onClick={handleTryAgain}>
                Try Again
              </Button>
            </div>
          )}

          {/* Input state */}
          {!result && (!error || onboard.isPending) && (
            <ImportUrlInput
              onImport={handleImport}
              isImporting={onboard.isPending}
              error={null}
            />
          )}
        </div>
      </div>
    </div>
  );
}

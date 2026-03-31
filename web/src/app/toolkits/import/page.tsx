"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { ArrowLeft, X } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Button } from "@/components/ui/button";
import { ImportUrlInput } from "@/components/toolkits/import-url-input";
import { DiscoveryResults } from "@/components/toolkits/discovery-results";
import { ImportConfirm } from "@/components/toolkits/import-confirm";
import { useDiscover, useImportToolkits } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { DiscoveryManifest } from "@/lib/types";

type Step = 1 | 2 | 3;

const STEPS = [
  { num: 1 as const, label: "Enter URL" },
  { num: 2 as const, label: "Review" },
  { num: 3 as const, label: "Import" },
];

function Stepper({ current }: { current: Step }) {
  return (
    <nav aria-label="Import progress" className="flex items-center gap-0">
      {STEPS.map((step, i) => (
        <div key={step.num} className="flex items-center">
          {i > 0 && (
            <div
              className={cn(
                "h-px w-10 sm:w-16",
                step.num <= current ? "bg-primary" : "bg-border",
              )}
            />
          )}
          <div className="flex items-center gap-2">
            <div
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-full text-xs font-medium transition-colors",
                step.num === current
                  ? "bg-primary text-primary-foreground"
                  : step.num < current
                    ? "bg-primary/20 text-primary"
                    : "bg-muted text-muted-foreground",
              )}
              aria-current={step.num === current ? "step" : undefined}
            >
              {step.num}
            </div>
            <span
              className={cn(
                "text-xs hidden sm:inline",
                step.num === current
                  ? "text-foreground font-medium"
                  : "text-muted-foreground",
              )}
            >
              {step.label}
            </span>
          </div>
        </div>
      ))}
    </nav>
  );
}

export default function ImportPage() {
  const [step, setStep] = useState<Step>(1);
  const [manifest, setManifest] = useState<DiscoveryManifest | null>(null);
  const [selectedComponentNames, setSelectedComponentNames] = useState<string[]>([]);
  const [importedCount, setImportedCount] = useState<number | null>(null);

  const discover = useDiscover();
  const importMutation = useImportToolkits();

  const handleDiscover = useCallback(
    (url: string, branch?: string) => {
      discover.mutate(
        { github_url: url, branch },
        {
          onSuccess: (data) => {
            setManifest(data);
            setStep(2);
          },
        },
      );
    },
    [discover],
  );

  const handleImport = useCallback(
    (componentNames: string[]) => {
      if (!manifest?.source_id) return;
      setSelectedComponentNames(componentNames);
      setStep(3);
      importMutation.mutate(
        {
          source_id: manifest.source_id,
          selected_components: componentNames,
        },
        {
          onSuccess: (data) => {
            setImportedCount(data.imported);
          },
        },
      );
    },
    [manifest, importMutation],
  );

  const handleRetry = useCallback(() => {
    if (!manifest?.source_id || selectedComponentNames.length === 0) return;
    importMutation.mutate(
      {
        source_id: manifest.source_id,
        selected_components: selectedComponentNames,
      },
      {
        onSuccess: (data) => {
          setImportedCount(data.imported);
        },
      },
    );
  }, [manifest, selectedComponentNames, importMutation]);

  const goBack = useCallback(() => {
    if (step === 2) {
      setStep(1);
      setManifest(null);
      discover.reset();
    } else if (step === 3 && !importMutation.isPending) {
      setStep(2);
      importMutation.reset();
      setImportedCount(null);
    }
  }, [step, discover, importMutation]);

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Top bar */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {step > 1 && !importMutation.isSuccess && (
              <Button
                variant="ghost"
                size="icon"
                onClick={goBack}
                disabled={importMutation.isPending}
                aria-label="Go back"
              >
                <ArrowLeft className="h-4 w-4" />
              </Button>
            )}
            <div>
              <h1 className="text-lg font-semibold text-foreground">
                Import from GitHub
              </h1>
              <p className="text-sm text-muted-foreground">
                Discover and import toolkits from a GitHub repository
              </p>
            </div>
          </div>
          <Link href="/toolkits">
            <Button variant="ghost" size="sm">
              <X className="h-4 w-4 mr-1" />
              Cancel
            </Button>
          </Link>
        </div>

        {/* Stepper */}
        <Stepper current={step} />

        {/* Step content */}
        <div className="max-w-2xl">
          {step === 1 && (
            <ImportUrlInput
              onDiscover={handleDiscover}
              isLoading={discover.isPending}
              error={
                discover.isError
                  ? (discover.error as Error)?.message ??
                    "Discovery failed. Please check the URL and try again."
                  : null
              }
            />
          )}

          {step === 2 && manifest && (
            <DiscoveryResults
              manifest={manifest}
              onImport={handleImport}
              isImporting={importMutation.isPending}
            />
          )}

          {step === 3 && (
            <ImportConfirm
              componentNames={selectedComponentNames}
              isLoading={importMutation.isPending}
              isSuccess={importMutation.isSuccess}
              isError={importMutation.isError}
              error={
                importMutation.isError
                  ? (importMutation.error as Error)?.message ??
                    "Import failed. Please try again."
                  : null
              }
              importedCount={importedCount}
              onRetry={handleRetry}
            />
          )}
        </div>
      </div>
    </div>
  );
}

"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { SourceTable } from "@/components/toolkits/source-table";
import {
  useToolkitSources,
  useSyncSource,
  useDeleteSource,
} from "@/lib/hooks";

export default function SourcesPage() {
  const { data, isLoading, isError } = useToolkitSources();
  const syncSource = useSyncSource();
  const deleteSource = useDeleteSource();
  const [syncingId, setSyncingId] = useState<string | null>(null);

  const handleSync = (id: string) => {
    setSyncingId(id);
    syncSource.mutate(id, {
      onSettled: () => setSyncingId(null),
    });
  };

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Navigation */}
        <Link href="/toolkits">
          <Button variant="ghost" size="sm" className="gap-1 -ml-2">
            <ArrowLeft className="h-4 w-4" />
            Back to Toolkits
          </Button>
        </Link>

        {/* Page header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              Toolkit Sources
            </h1>
            <p className="text-sm text-muted-foreground">
              GitHub repositories registered for toolkit discovery
            </p>
          </div>
          {data && (
            <span className="text-xs text-muted-foreground font-mono">
              {data.total} source{data.total !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {/* Table */}
        <Card>
          <CardContent className="p-0">
            <SourceTable
              sources={data?.sources || []}
              isLoading={isLoading}
              isError={isError}
              onSync={handleSync}
              onDelete={(id) => deleteSource.mutate(id)}
              syncingId={syncingId}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

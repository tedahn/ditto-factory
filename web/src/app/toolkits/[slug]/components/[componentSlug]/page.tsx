"use client";

import { use, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2, FileText, ChevronDown, ChevronRight } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToolkitComponent } from "@/lib/hooks";
import { ComponentType, RiskLevel, LoadStrategy } from "@/lib/types";

const COMPONENT_TYPE_COLORS: Record<ComponentType, string> = {
  [ComponentType.SKILL]: "bg-purple-500/10 text-purple-400 ring-purple-500/20",
  [ComponentType.PLUGIN]: "bg-blue-500/10 text-blue-400 ring-blue-500/20",
  [ComponentType.PROFILE]: "bg-emerald-500/10 text-emerald-400 ring-emerald-500/20",
  [ComponentType.TOOL]: "bg-orange-500/10 text-orange-400 ring-orange-500/20",
  [ComponentType.AGENT]: "bg-cyan-500/10 text-cyan-400 ring-cyan-500/20",
  [ComponentType.COMMAND]: "bg-pink-500/10 text-pink-400 ring-pink-500/20",
};

const RISK_COLORS: Record<RiskLevel, string> = {
  [RiskLevel.SAFE]: "text-green-400",
  [RiskLevel.MODERATE]: "text-yellow-400",
  [RiskLevel.HIGH]: "text-red-400",
};

const LOAD_STRATEGY_LABELS: Record<LoadStrategy, string> = {
  [LoadStrategy.MOUNT_FILE]: "Mount File",
  [LoadStrategy.INSTALL_PLUGIN]: "Install Plugin",
  [LoadStrategy.INJECT_RULES]: "Inject Rules",
  [LoadStrategy.INSTALL_PACKAGE]: "Install Package",
};

function MetadataRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 border-b border-border/50 last:border-0">
      <span className="text-xs text-muted-foreground shrink-0">{label}</span>
      <div className="text-sm text-foreground text-right">{children}</div>
    </div>
  );
}

export default function ComponentDetailPage({
  params,
}: {
  params: Promise<{ slug: string; componentSlug: string }>;
}) {
  const { slug, componentSlug } = use(params);
  const { data: component, isLoading, isError } = useToolkitComponent(slug, componentSlug);
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());

  const toggleFile = (fileId: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(fileId)) {
        next.delete(fileId);
      } else {
        next.add(fileId);
      }
      return next;
    });
  };

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Back link */}
        <Link
          href={`/toolkits/${slug}`}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Toolkit
        </Link>

        {/* Loading */}
        {isLoading && (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            <span className="ml-2 text-sm text-muted-foreground">Loading component...</span>
          </div>
        )}

        {/* Error */}
        {isError && (
          <div className="py-16 text-center">
            <p className="text-sm text-destructive-foreground">
              Failed to load component. It may not exist or the controller may be down.
            </p>
          </div>
        )}

        {/* Loaded */}
        {component && (
          <>
            {/* Header */}
            <div className="flex items-center gap-3">
              <h1 className="text-lg font-semibold text-foreground">{component.name}</h1>
              <Badge className={`text-[10px] ${COMPONENT_TYPE_COLORS[component.type] ?? ""}`}>
                {component.type}
              </Badge>
            </div>

            {component.description && (
              <p className="text-sm text-muted-foreground -mt-4">{component.description}</p>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
              {/* Left: Content viewer (60%) */}
              <div className="lg:col-span-3">
                <div className="rounded-lg border border-border bg-[#0d1117] overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-2 border-b border-border/50 bg-[#161b22]">
                    <span className="text-xs text-muted-foreground font-mono">
                      {component.primary_file}
                    </span>
                  </div>
                  <div className="p-4 overflow-x-auto">
                    <pre className="text-sm font-mono text-gray-300 leading-relaxed whitespace-pre-wrap break-words">
                      {component.content || "No content available."}
                    </pre>
                  </div>
                </div>
              </div>

              {/* Right: Metadata (40%) */}
              <div className="lg:col-span-2">
                <Card>
                  <CardHeader>
                    <CardTitle>Metadata</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-0">
                    <MetadataRow label="Type">
                      <Badge className={`text-[10px] ${COMPONENT_TYPE_COLORS[component.type] ?? ""}`}>
                        {component.type}
                      </Badge>
                    </MetadataRow>

                    <MetadataRow label="Directory">
                      <span className="font-mono text-xs">{component.directory}</span>
                    </MetadataRow>

                    <MetadataRow label="Risk Level">
                      <span className={`text-xs capitalize ${RISK_COLORS[component.risk_level]}`}>
                        {component.risk_level}
                      </span>
                    </MetadataRow>

                    <MetadataRow label="Load Strategy">
                      <span className="text-xs">
                        {LOAD_STRATEGY_LABELS[component.load_strategy] ?? component.load_strategy}
                      </span>
                    </MetadataRow>

                    <MetadataRow label="Active">
                      <span className="text-xs">{component.is_active ? "Yes" : "No"}</span>
                    </MetadataRow>

                    {component.tags.length > 0 && (
                      <MetadataRow label="Tags">
                        <div className="flex flex-wrap justify-end gap-1">
                          {component.tags.map((tag) => (
                            <Badge key={tag} variant="secondary" className="text-[10px]">
                              {tag}
                            </Badge>
                          ))}
                        </div>
                      </MetadataRow>
                    )}

                    <MetadataRow label="Files">
                      <span className="font-mono text-xs">{component.file_count}</span>
                    </MetadataRow>
                  </CardContent>
                </Card>
              </div>
            </div>

            {/* Files list */}
            {component.files && component.files.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Files ({component.files.length})</CardTitle>
                </CardHeader>
                <CardContent className="space-y-1">
                  {component.files.map((file) => (
                    <div key={file.id || file.path} className="rounded border border-border/50">
                      <button
                        type="button"
                        onClick={() => toggleFile(file.id)}
                        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-muted/50 transition-colors"
                      >
                        {expandedFiles.has(file.id) ? (
                          <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />
                        ) : (
                          <ChevronRight className="h-3 w-3 text-muted-foreground shrink-0" />
                        )}
                        <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                        <span className="text-xs font-mono text-foreground truncate">
                          {file.path}
                        </span>
                        {file.is_primary && (
                          <Badge variant="secondary" className="text-[10px] ml-auto shrink-0">
                            primary
                          </Badge>
                        )}
                      </button>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  );
}

"use client";

import { ExternalLink, AlertTriangle, ShieldAlert, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Toolkit } from "@/lib/types";
import { ToolkitType, ToolkitStatus, RiskLevel, LoadStrategy } from "@/lib/types";

const TYPE_BADGE_COLORS: Record<ToolkitType, string> = {
  [ToolkitType.SKILL]: "bg-purple-500/15 text-purple-400 border-purple-500/20",
  [ToolkitType.PLUGIN]: "bg-blue-500/15 text-blue-400 border-blue-500/20",
  [ToolkitType.PROFILE]: "bg-green-500/15 text-green-400 border-green-500/20",
  [ToolkitType.TOOL]: "bg-orange-500/15 text-orange-400 border-orange-500/20",
};

const STATUS_BADGE_COLORS: Record<ToolkitStatus, string> = {
  [ToolkitStatus.AVAILABLE]: "bg-green-500/15 text-green-400 border-green-500/20",
  [ToolkitStatus.DISABLED]: "bg-gray-500/15 text-gray-400 border-gray-500/20",
  [ToolkitStatus.UPDATE_AVAILABLE]: "bg-yellow-500/15 text-yellow-400 border-yellow-500/20",
  [ToolkitStatus.ERROR]: "bg-red-500/15 text-red-400 border-red-500/20",
};

const LOAD_STRATEGY_LABELS: Record<LoadStrategy, string> = {
  [LoadStrategy.MOUNT_FILE]: "Mount File",
  [LoadStrategy.INSTALL_PLUGIN]: "Install Plugin",
  [LoadStrategy.INJECT_RULES]: "Inject Rules",
  [LoadStrategy.INSTALL_PACKAGE]: "Install Package",
};

function RiskIcon({ level }: { level: RiskLevel }) {
  switch (level) {
    case RiskLevel.SAFE:
      return <ShieldCheck className="h-3.5 w-3.5 text-green-400" />;
    case RiskLevel.MODERATE:
      return <AlertTriangle className="h-3.5 w-3.5 text-yellow-400" />;
    case RiskLevel.HIGH:
      return <ShieldAlert className="h-3.5 w-3.5 text-red-400" />;
  }
}

const RISK_LABEL_COLORS: Record<RiskLevel, string> = {
  [RiskLevel.SAFE]: "text-green-400",
  [RiskLevel.MODERATE]: "text-yellow-400",
  [RiskLevel.HIGH]: "text-red-400",
};

function formatTimestamp(dateStr: string | null | undefined): string {
  if (!dateStr) return "--";
  return new Date(dateStr).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function MetadataRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 border-b border-border/50 last:border-0">
      <span className="text-xs text-muted-foreground shrink-0">{label}</span>
      <div className="text-sm text-foreground text-right">{children}</div>
    </div>
  );
}

interface ToolkitDetailProps {
  toolkit: Toolkit;
  isDisabling: boolean;
  isDeleting: boolean;
  onToggleStatus: () => void;
  onDelete: () => void;
}

export function ToolkitDetail({
  toolkit,
  isDisabling,
  isDeleting,
  onToggleStatus,
  onDelete,
}: ToolkitDetailProps) {
  const isDisabled = toolkit.status === ToolkitStatus.DISABLED;

  // Parse source info from path (owner/repo pattern)
  const pathParts = toolkit.path.split("/");
  const sourceDisplay =
    pathParts.length >= 2 ? `${pathParts[0]}/${pathParts[1]}` : toolkit.path;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
      {/* Left column: Content viewer (60%) */}
      <div className="lg:col-span-3">
        <div className="rounded-lg border border-border bg-[#0d1117] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 border-b border-border/50 bg-[#161b22]">
            <span className="text-xs text-muted-foreground font-mono">
              {toolkit.path}
            </span>
            <Badge variant="info" className="text-[10px]">
              v{toolkit.version}
            </Badge>
          </div>
          <div className="p-4 overflow-x-auto">
            <pre className="text-sm font-mono text-gray-300 leading-relaxed whitespace-pre-wrap break-words">
              {toolkit.content || "No content available."}
            </pre>
          </div>
        </div>
      </div>

      {/* Right column: Metadata panel (40%) */}
      <div className="lg:col-span-2">
        <Card>
          <CardHeader>
            <CardTitle>Metadata</CardTitle>
          </CardHeader>
          <CardContent className="space-y-0">
            <MetadataRow label="Type">
              <Badge
                variant="secondary"
                className={TYPE_BADGE_COLORS[toolkit.type]}
              >
                {toolkit.type}
              </Badge>
            </MetadataRow>

            <MetadataRow label="Source">
              <span className="font-mono text-xs text-primary hover:underline">
                {sourceDisplay}
              </span>
            </MetadataRow>

            <MetadataRow label="Path">
              <span className="font-mono text-xs">{toolkit.path}</span>
            </MetadataRow>

            <MetadataRow label="Version">
              <Badge variant="info">v{toolkit.version}</Badge>
            </MetadataRow>

            <MetadataRow label="Pinned SHA">
              <span className="font-mono text-xs">
                {toolkit.pinned_sha
                  ? toolkit.pinned_sha.slice(0, 7)
                  : "--"}
              </span>
            </MetadataRow>

            {toolkit.tags.length > 0 && (
              <MetadataRow label="Tags">
                <div className="flex flex-wrap justify-end gap-1">
                  {toolkit.tags.map((tag) => (
                    <Badge key={tag} variant="secondary" className="text-[10px]">
                      {tag}
                    </Badge>
                  ))}
                </div>
              </MetadataRow>
            )}

            {toolkit.dependencies.length > 0 && (
              <MetadataRow label="Dependencies">
                <div className="flex flex-col items-end gap-0.5">
                  {toolkit.dependencies.map((dep) => (
                    <span key={dep} className="font-mono text-xs">
                      {dep}
                    </span>
                  ))}
                </div>
              </MetadataRow>
            )}

            <MetadataRow label="Risk Level">
              <div className="flex items-center gap-1.5">
                <RiskIcon level={toolkit.risk_level} />
                <span
                  className={`text-xs capitalize ${RISK_LABEL_COLORS[toolkit.risk_level]}`}
                >
                  {toolkit.risk_level}
                </span>
              </div>
            </MetadataRow>

            <MetadataRow label="Status">
              <Badge
                variant="secondary"
                className={STATUS_BADGE_COLORS[toolkit.status]}
              >
                {toolkit.status.replace("_", " ")}
              </Badge>
            </MetadataRow>

            <MetadataRow label="Usage Count">
              <span className="font-mono text-xs">
                {toolkit.usage_count ?? 0}
              </span>
            </MetadataRow>

            <MetadataRow label="Created">
              <span className="text-xs">
                {formatTimestamp(toolkit.created_at)}
              </span>
            </MetadataRow>

            <MetadataRow label="Updated">
              <span className="text-xs">
                {formatTimestamp(toolkit.updated_at)}
              </span>
            </MetadataRow>

            <MetadataRow label="Load Strategy">
              <span className="text-xs">
                {LOAD_STRATEGY_LABELS[toolkit.load_strategy] ??
                  toolkit.load_strategy}
              </span>
            </MetadataRow>

            {/* Action buttons */}
            <div className="flex items-center gap-2 pt-4">
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={onToggleStatus}
                disabled={isDisabling}
              >
                {isDisabled ? "Enable" : "Disable"}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                className="flex-1"
                onClick={onDelete}
                disabled={isDeleting}
              >
                Delete
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

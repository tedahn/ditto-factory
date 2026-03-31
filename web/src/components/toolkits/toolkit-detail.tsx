"use client";

import Link from "next/link";
import { ExternalLink, FileText } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ToolkitDetail as ToolkitDetailType } from "@/lib/types";
import { ToolkitCategory, ToolkitStatus, ComponentType } from "@/lib/types";

const CATEGORY_BADGE_COLORS: Record<ToolkitCategory, string> = {
  [ToolkitCategory.SKILL_COLLECTION]: "bg-purple-500/15 text-purple-400 border-purple-500/20",
  [ToolkitCategory.PLUGIN]: "bg-blue-500/15 text-blue-400 border-blue-500/20",
  [ToolkitCategory.PROFILE_PACK]: "bg-green-500/15 text-green-400 border-green-500/20",
  [ToolkitCategory.TOOL]: "bg-orange-500/15 text-orange-400 border-orange-500/20",
  [ToolkitCategory.MIXED]: "bg-gray-500/15 text-gray-400 border-gray-500/20",
};

const STATUS_BADGE_COLORS: Record<ToolkitStatus, string> = {
  [ToolkitStatus.AVAILABLE]: "bg-green-500/15 text-green-400 border-green-500/20",
  [ToolkitStatus.DISABLED]: "bg-gray-500/15 text-gray-400 border-gray-500/20",
  [ToolkitStatus.UPDATE_AVAILABLE]: "bg-yellow-500/15 text-yellow-400 border-yellow-500/20",
  [ToolkitStatus.ERROR]: "bg-red-500/15 text-red-400 border-red-500/20",
};

const COMPONENT_TYPE_COLORS: Record<ComponentType, string> = {
  [ComponentType.SKILL]: "bg-purple-500/10 text-purple-400 ring-purple-500/20",
  [ComponentType.PLUGIN]: "bg-blue-500/10 text-blue-400 ring-blue-500/20",
  [ComponentType.PROFILE]: "bg-emerald-500/10 text-emerald-400 ring-emerald-500/20",
  [ComponentType.TOOL]: "bg-orange-500/10 text-orange-400 ring-orange-500/20",
  [ComponentType.AGENT]: "bg-cyan-500/10 text-cyan-400 ring-cyan-500/20",
  [ComponentType.COMMAND]: "bg-pink-500/10 text-pink-400 ring-pink-500/20",
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
  toolkit: ToolkitDetailType;
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

  return (
    <div className="space-y-6">
      {/* Metadata panel */}
      <Card>
        <CardHeader>
          <CardTitle>Toolkit Info</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0">
          <MetadataRow label="Category">
            <Badge
              variant="secondary"
              className={CATEGORY_BADGE_COLORS[toolkit.category]}
            >
              {toolkit.category.replace(/_/g, " ")}
            </Badge>
          </MetadataRow>

          <MetadataRow label="Source">
            {toolkit.source_owner && toolkit.source_repo ? (
              <a
                href={`https://github.com/${toolkit.source_owner}/${toolkit.source_repo}`}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-xs text-primary hover:underline inline-flex items-center gap-1"
              >
                {toolkit.source_owner}/{toolkit.source_repo}
                <ExternalLink className="h-3 w-3" />
              </a>
            ) : (
              <span className="font-mono text-xs">--</span>
            )}
          </MetadataRow>

          <MetadataRow label="Version">
            <Badge variant="info">v{toolkit.version}</Badge>
          </MetadataRow>

          <MetadataRow label="Pinned SHA">
            <span className="font-mono text-xs">
              {toolkit.pinned_sha ? toolkit.pinned_sha.slice(0, 7) : "--"}
            </span>
          </MetadataRow>

          <MetadataRow label="Components">
            <span className="font-mono text-xs">{toolkit.component_count}</span>
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

          <MetadataRow label="Status">
            <Badge
              variant="secondary"
              className={STATUS_BADGE_COLORS[toolkit.status]}
            >
              {toolkit.status.replace("_", " ")}
            </Badge>
          </MetadataRow>

          <MetadataRow label="Created">
            <span className="text-xs">{formatTimestamp(toolkit.created_at)}</span>
          </MetadataRow>

          <MetadataRow label="Updated">
            <span className="text-xs">{formatTimestamp(toolkit.updated_at)}</span>
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

      {/* Component grid */}
      {toolkit.components && toolkit.components.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">
            Components ({toolkit.components.length})
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {toolkit.components.map((comp) => (
              <Card key={comp.id || comp.slug} className="hover:border-muted-foreground/30 transition-colors">
                <CardContent className="p-4 space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm text-foreground truncate">
                      {comp.name}
                    </span>
                    <Badge
                      className={`text-[10px] ${COMPONENT_TYPE_COLORS[comp.type] ?? ""}`}
                    >
                      {comp.type}
                    </Badge>
                  </div>
                  {comp.description && (
                    <p className="text-xs text-muted-foreground line-clamp-2">
                      {comp.description}
                    </p>
                  )}
                  <div className="flex items-center justify-between pt-1">
                    <span className="text-xs text-muted-foreground inline-flex items-center gap-1">
                      <FileText className="h-3 w-3" />
                      {comp.file_count} file{comp.file_count !== 1 ? "s" : ""}
                    </span>
                    <Link
                      href={`/toolkits/${toolkit.slug}/components/${comp.slug}`}
                      className="text-xs text-primary hover:underline"
                    >
                      View
                    </Link>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

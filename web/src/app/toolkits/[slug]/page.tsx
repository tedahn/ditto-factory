"use client";

import { use } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, Zap, ZapOff } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ToolkitDetail } from "@/components/toolkits/toolkit-detail";
import { ToolkitVersions } from "@/components/toolkits/toolkit-versions";
import { ToolkitUpdate } from "@/components/toolkits/toolkit-update";
import {
  useToolkit,
  useToolkitVersions,
  useRollbackToolkit,
  useApplyToolkitUpdate,
  useDeleteToolkit,
  useActivateToolkit,
  useDeactivateToolkit,
} from "@/lib/hooks";
import { ToolkitCategory, ToolkitStatus, ComponentType } from "@/lib/types";
import { apiPost } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/hooks";

const CATEGORY_COLORS: Record<ToolkitCategory, string> = {
  [ToolkitCategory.SKILL_COLLECTION]: "bg-purple-500/15 text-purple-400 border-purple-500/20",
  [ToolkitCategory.PLUGIN]: "bg-blue-500/15 text-blue-400 border-blue-500/20",
  [ToolkitCategory.PROFILE_PACK]: "bg-green-500/15 text-green-400 border-green-500/20",
  [ToolkitCategory.TOOL]: "bg-orange-500/15 text-orange-400 border-orange-500/20",
  [ToolkitCategory.MIXED]: "bg-gray-500/15 text-gray-400 border-gray-500/20",
};

export default function ToolkitDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const router = useRouter();
  const queryClient = useQueryClient();

  const { data: toolkit, isLoading, isError } = useToolkit(slug);
  const {
    data: versions,
    isLoading: versionsLoading,
  } = useToolkitVersions(slug);

  const rollback = useRollbackToolkit(slug);
  const applyUpdate = useApplyToolkitUpdate(slug);
  const deleteToolkit = useDeleteToolkit();
  const activateToolkit = useActivateToolkit();
  const deactivateToolkit = useDeactivateToolkit();

  // Check if toolkit has activatable components (skills or agents)
  const activatableComponents = toolkit?.components?.filter(
    (c) => c.type === ComponentType.SKILL || c.type === ComponentType.AGENT,
  ) ?? [];
  const hasActivatableComponents = activatableComponents.length > 0;
  const activeCount = activatableComponents.filter((c) => c.is_active).length;
  const isActivated = activeCount > 0;

  const handleActivate = () => {
    activateToolkit.mutate(slug, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: queryKeys.toolkit(slug) });
      },
    });
  };

  const handleDeactivate = () => {
    if (
      window.confirm(
        `Deactivate all skills from "${toolkit?.name}"? They will no longer be available for tasks.`,
      )
    ) {
      deactivateToolkit.mutate(slug, {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: queryKeys.toolkit(slug) });
        },
      });
    }
  };

  const handleToggleStatus = async () => {
    if (!toolkit) return;
    try {
      const newStatus =
        toolkit.status === ToolkitStatus.DISABLED ? "available" : "disabled";
      await apiPost(`/api/v1/toolkits/${slug}/status`, { status: newStatus });
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkit(slug) });
      queryClient.invalidateQueries({ queryKey: queryKeys.toolkits });
    } catch {
      // Silently fail
    }
  };

  const handleDelete = () => {
    if (!toolkit) return;
    if (
      window.confirm(
        `Delete toolkit "${toolkit.name}"? This action cannot be undone.`,
      )
    ) {
      deleteToolkit.mutate(slug, {
        onSuccess: () => router.push("/toolkits"),
      });
    }
  };

  const handleRollback = (version: number) => {
    if (
      window.confirm(`Rollback to version ${version}? This will change the active content.`)
    ) {
      rollback.mutate(version);
    }
  };

  const handleApplyUpdate = () => {
    applyUpdate.mutate();
  };

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Back link */}
        <Link
          href="/toolkits"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Toolkits
        </Link>

        {/* Loading state */}
        {isLoading && (
          <div className="space-y-6">
            <div className="h-8 w-64 rounded bg-muted animate-pulse" />
            <div className="h-96 rounded-lg bg-muted animate-pulse" />
          </div>
        )}

        {/* Error state */}
        {isError && (
          <div className="py-16 text-center">
            <p className="text-sm text-destructive-foreground">
              Failed to load toolkit. It may not exist or the controller may be
              down.
            </p>
          </div>
        )}

        {/* Loaded state */}
        {toolkit && (
          <>
            {/* Page header */}
            <div className="flex items-center gap-3">
              <h1 className="text-lg font-semibold text-foreground">
                {toolkit.name}
              </h1>
              <Badge
                variant="secondary"
                className={CATEGORY_COLORS[toolkit.category]}
              >
                {toolkit.category.replace(/_/g, " ")}
              </Badge>
              {toolkit.source_owner && toolkit.source_repo && (
                <span className="text-xs text-muted-foreground font-mono">
                  {toolkit.source_owner}/{toolkit.source_repo}
                </span>
              )}
            </div>

            {/* Activation Controls */}
            {hasActivatableComponents && (
              <div className="flex items-center gap-3 -mt-2">
                {isActivated ? (
                  <>
                    <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
                      <span className="h-2 w-2 rounded-full bg-emerald-400" />
                      {activeCount} skill{activeCount !== 1 ? "s" : ""} activated
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleDeactivate}
                      disabled={deactivateToolkit.isPending}
                      className="gap-1.5 text-xs border-red-500/30 text-red-400 hover:bg-red-500/10"
                    >
                      {deactivateToolkit.isPending ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <ZapOff className="h-3 w-3" />
                      )}
                      Deactivate
                    </Button>
                  </>
                ) : (
                  <>
                    <span className="text-xs text-muted-foreground">
                      Not activated
                    </span>
                    <Button
                      variant="default"
                      size="sm"
                      onClick={handleActivate}
                      disabled={activateToolkit.isPending}
                      className="gap-1.5 text-xs"
                    >
                      {activateToolkit.isPending ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Zap className="h-3 w-3" />
                      )}
                      Activate Skills
                    </Button>
                  </>
                )}
              </div>
            )}

            {toolkit.description && (
              <p className="text-sm text-muted-foreground -mt-4">
                {toolkit.description}
              </p>
            )}

            {/* Update banner */}
            {toolkit.status === ToolkitStatus.UPDATE_AVAILABLE && (
              <ToolkitUpdate
                slug={slug}
                isApplying={applyUpdate.isPending}
                onApply={handleApplyUpdate}
              />
            )}

            {/* Main detail with component grid */}
            <ToolkitDetail
              toolkit={toolkit}
              isDisabling={false}
              isDeleting={deleteToolkit.isPending}
              onToggleStatus={handleToggleStatus}
              onDelete={handleDelete}
            />

            {/* Version history */}
            <Card>
              <CardHeader>
                <CardTitle>Version History</CardTitle>
              </CardHeader>
              <CardContent>
                <ToolkitVersions
                  versions={versions ?? []}
                  currentVersion={toolkit.version}
                  isLoading={versionsLoading}
                  isRollingBack={rollback.isPending}
                  onRollback={handleRollback}
                />
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

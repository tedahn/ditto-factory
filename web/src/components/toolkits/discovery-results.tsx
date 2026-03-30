"use client";

import { useState, useCallback, useMemo } from "react";
import { Check, ChevronDown, ChevronRight, AlertTriangle, Shield, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { DiscoveryManifest, DiscoveredItem, ToolkitType, RiskLevel } from "@/lib/types";

interface DiscoveryResultsProps {
  manifest: DiscoveryManifest;
  onImport: (items: DiscoveredItem[]) => void;
  isImporting: boolean;
}

const TYPE_COLORS: Record<string, string> = {
  skill: "bg-purple-500/10 text-purple-400 ring-purple-500/20",
  plugin: "bg-blue-500/10 text-blue-400 ring-blue-500/20",
  profile: "bg-emerald-500/10 text-emerald-400 ring-emerald-500/20",
  tool: "bg-orange-500/10 text-orange-400 ring-orange-500/20",
};

const RISK_CONFIG: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  safe: {
    icon: <Check className="h-3.5 w-3.5" />,
    color: "text-emerald-400",
    label: "Safe",
  },
  moderate: {
    icon: <AlertTriangle className="h-3.5 w-3.5" />,
    color: "text-amber-400",
    label: "Moderate",
  },
  high: {
    icon: <ShieldAlert className="h-3.5 w-3.5" />,
    color: "text-red-400",
    label: "High risk",
  },
};

function RiskIndicator({ level }: { level: RiskLevel | string }) {
  const config = RISK_CONFIG[level] ?? RISK_CONFIG.safe;
  return (
    <span
      className={cn("inline-flex items-center gap-1 text-xs", config.color)}
      title={config.label}
    >
      {config.icon}
      <span>{config.label}</span>
    </span>
  );
}

interface ItemCardProps {
  item: DiscoveredItem;
  selected: boolean;
  onToggle: () => void;
}

function ItemCard({ item, selected, onToggle }: ItemCardProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={cn(
        "rounded-lg border p-4 transition-colors",
        selected
          ? "border-primary/40 bg-primary/5"
          : "border-border bg-card hover:border-muted-foreground/30",
      )}
    >
      <div className="flex items-start gap-3">
        {/* Checkbox */}
        <button
          type="button"
          role="checkbox"
          aria-checked={selected}
          onClick={onToggle}
          className={cn(
            "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border transition-colors",
            selected
              ? "border-primary bg-primary text-primary-foreground"
              : "border-muted-foreground/40 hover:border-muted-foreground",
          )}
        >
          {selected && <Check className="h-3.5 w-3.5" />}
        </button>

        <div className="flex-1 min-w-0 space-y-1.5">
          {/* Name + type + risk */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-foreground">{item.name}</span>
            <Badge
              className={cn(
                "text-[10px] uppercase tracking-wider",
                TYPE_COLORS[item.type] ?? "",
              )}
            >
              {item.type}
            </Badge>
            <RiskIndicator level={item.risk_level} />
          </div>

          {/* Description */}
          {item.description && (
            <p className="text-sm text-muted-foreground line-clamp-2">
              {item.description}
            </p>
          )}

          {/* Path */}
          <p className="text-xs text-muted-foreground/60 font-mono truncate">
            {item.path}
          </p>

          {/* Tags */}
          {item.tags.length > 0 && (
            <div className="flex gap-1 flex-wrap">
              {item.tags.map((tag) => (
                <Badge key={tag} variant="secondary" className="text-[10px]">
                  {tag}
                </Badge>
              ))}
            </div>
          )}

          {/* Dependencies */}
          {item.dependencies.length > 0 && (
            <p className="text-xs text-muted-foreground">
              <span className="font-medium">Depends on:</span>{" "}
              {item.dependencies.join(", ")}
            </p>
          )}

          {/* Expandable preview - only show toggle if there could be content */}
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors mt-1"
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            Preview content
          </button>
          {expanded && (
            <div className="mt-2 rounded-md bg-zinc-900 border border-zinc-800 p-3 max-h-[300px] overflow-auto">
              <pre className="text-xs text-zinc-300 font-mono whitespace-pre-wrap break-words">
                {item.description || "(no content preview available)"}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function DiscoveryResults({
  manifest,
  onImport,
  isImporting,
}: DiscoveryResultsProps) {
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(() => {
    // Select all items by default (recommended items = all for now)
    return new Set(manifest.discovered.map((d) => d.path));
  });

  const allSelected = selectedPaths.size === manifest.discovered.length;
  const noneSelected = selectedPaths.size === 0;

  const toggleAll = useCallback(() => {
    if (allSelected) {
      setSelectedPaths(new Set());
    } else {
      setSelectedPaths(new Set(manifest.discovered.map((d) => d.path)));
    }
  }, [allSelected, manifest.discovered]);

  const toggleItem = useCallback((path: string) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  const selectedItems = useMemo(
    () => manifest.discovered.filter((d) => selectedPaths.has(d.path)),
    [manifest.discovered, selectedPaths],
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="rounded-lg border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-3 flex-wrap">
          <h3 className="font-semibold text-foreground">
            {manifest.owner}/{manifest.repo}
          </h3>
          <Badge variant="secondary" className="font-mono text-[10px]">
            {manifest.branch}
          </Badge>
          <span className="text-xs text-muted-foreground font-mono">
            {manifest.commit_sha.slice(0, 8)}
          </span>
        </div>
        <p className="text-sm text-muted-foreground">
          {manifest.discovered.length} item{manifest.discovered.length !== 1 ? "s" : ""} discovered
        </p>
      </div>

      {/* Select all toggle */}
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={toggleAll}
          className="text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          {allSelected ? "Deselect all" : "Select all"}
        </button>
        <span className="text-xs text-muted-foreground">
          {selectedPaths.size} of {manifest.discovered.length} selected
        </span>
      </div>

      {/* Item list */}
      <div className="space-y-2">
        {manifest.discovered.map((item) => (
          <ItemCard
            key={item.path}
            item={item}
            selected={selectedPaths.has(item.path)}
            onToggle={() => toggleItem(item.path)}
          />
        ))}
      </div>

      {/* Summary bar */}
      <div className="sticky bottom-0 rounded-lg border border-border bg-card/95 backdrop-blur p-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">
          {selectedPaths.size} of {manifest.discovered.length} items selected
        </span>
        <Button
          onClick={() => onImport(selectedItems)}
          disabled={noneSelected || isImporting}
        >
          {isImporting ? "Importing..." : "Import Selected"}
        </Button>
      </div>
    </div>
  );
}

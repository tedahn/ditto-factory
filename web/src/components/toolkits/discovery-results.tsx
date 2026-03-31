"use client";

import { useState, useCallback, useMemo } from "react";
import { Check, ChevronDown, ChevronRight, AlertTriangle, ShieldAlert, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { DiscoveryManifest, DiscoveredComponent, ComponentType, RiskLevel, ToolkitCategory } from "@/lib/types";

interface DiscoveryResultsProps {
  manifest: DiscoveryManifest;
  onImport: (componentNames: string[]) => void;
  isImporting: boolean;
}

const TYPE_COLORS: Record<string, string> = {
  skill: "bg-purple-500/10 text-purple-400 ring-purple-500/20",
  plugin: "bg-blue-500/10 text-blue-400 ring-blue-500/20",
  profile: "bg-emerald-500/10 text-emerald-400 ring-emerald-500/20",
  tool: "bg-orange-500/10 text-orange-400 ring-orange-500/20",
  agent: "bg-cyan-500/10 text-cyan-400 ring-cyan-500/20",
  command: "bg-pink-500/10 text-pink-400 ring-pink-500/20",
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

interface ComponentCardProps {
  component: DiscoveredComponent;
  selected: boolean;
  onToggle: () => void;
}

function ComponentCard({ component, selected, onToggle }: ComponentCardProps) {
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
            <span className="font-semibold text-foreground">{component.name}</span>
            <Badge
              className={cn(
                "text-[10px] uppercase tracking-wider",
                TYPE_COLORS[component.type] ?? "",
              )}
            >
              {component.type}
            </Badge>
            <RiskIndicator level={component.risk_level} />
          </div>

          {/* Description */}
          {component.description && (
            <p className="text-sm text-muted-foreground line-clamp-2">
              {component.description}
            </p>
          )}

          {/* Directory + file count */}
          <div className="flex items-center gap-3">
            <p className="text-xs text-muted-foreground/60 font-mono truncate">
              {component.directory}
            </p>
            <span className="text-xs text-muted-foreground inline-flex items-center gap-1">
              <FileText className="h-3 w-3" />
              {component.files.length} file{component.files.length !== 1 ? "s" : ""}
            </span>
          </div>

          {/* Tags */}
          {component.tags.length > 0 && (
            <div className="flex gap-1 flex-wrap">
              {component.tags.map((tag) => (
                <Badge key={tag} variant="secondary" className="text-[10px]">
                  {tag}
                </Badge>
              ))}
            </div>
          )}

          {/* Expandable file list */}
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
            {component.files.length} file{component.files.length !== 1 ? "s" : ""}
          </button>
          {expanded && (
            <div className="mt-2 rounded-md bg-zinc-900 border border-zinc-800 p-3 space-y-1">
              {component.files.map((file) => (
                <div key={file.path} className="flex items-center gap-2">
                  <FileText className="h-3 w-3 text-zinc-500 shrink-0" />
                  <span className="text-xs text-zinc-300 font-mono truncate">
                    {file.path}
                  </span>
                  {file.is_primary && (
                    <Badge variant="secondary" className="text-[9px]">primary</Badge>
                  )}
                </div>
              ))}
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
  const [selectedNames, setSelectedNames] = useState<Set<string>>(() => {
    return new Set(manifest.discovered.map((d) => d.name));
  });

  const allSelected = selectedNames.size === manifest.discovered.length;
  const noneSelected = selectedNames.size === 0;

  const toggleAll = useCallback(() => {
    if (allSelected) {
      setSelectedNames(new Set());
    } else {
      setSelectedNames(new Set(manifest.discovered.map((d) => d.name)));
    }
  }, [allSelected, manifest.discovered]);

  const toggleComponent = useCallback((name: string) => {
    setSelectedNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  }, []);

  const selectedComponentNames = useMemo(
    () => Array.from(selectedNames),
    [selectedNames],
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
        {manifest.repo_description && (
          <p className="text-sm text-muted-foreground">{manifest.repo_description}</p>
        )}
        <p className="text-sm text-muted-foreground">
          {manifest.discovered.length} component{manifest.discovered.length !== 1 ? "s" : ""} discovered
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
          {selectedNames.size} of {manifest.discovered.length} selected
        </span>
      </div>

      {/* Component list */}
      <div className="space-y-2">
        {manifest.discovered.map((component) => (
          <ComponentCard
            key={component.name}
            component={component}
            selected={selectedNames.has(component.name)}
            onToggle={() => toggleComponent(component.name)}
          />
        ))}
      </div>

      {/* Summary bar */}
      <div className="sticky bottom-0 rounded-lg border border-border bg-card/95 backdrop-blur p-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">
          {selectedNames.size} of {manifest.discovered.length} components selected
        </span>
        <Button
          onClick={() => onImport(selectedComponentNames)}
          disabled={noneSelected || isImporting}
        >
          {isImporting ? "Importing..." : "Import Selected"}
        </Button>
      </div>
    </div>
  );
}

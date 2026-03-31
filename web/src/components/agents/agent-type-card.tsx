"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { AgentTypeSummary } from "@/lib/types";

interface AgentTypeCardProps {
  agentType: AgentTypeSummary;
  isExpanded: boolean;
  onToggle: () => void;
}

export function AgentTypeCard({ agentType, isExpanded, onToggle }: AgentTypeCardProps) {
  return (
    <Card
      className={`cursor-pointer transition-colors hover:bg-accent/50 ${isExpanded ? "ring-2 ring-primary" : ""}`}
      onClick={onToggle}
    >
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-sm">{agentType.name}</h3>
          <div className="flex gap-1.5">
            {agentType.is_default && (
              <Badge variant="secondary" className="text-xs">Default</Badge>
            )}
            <Badge variant="secondary" className="text-xs font-mono">
              {agentType.job_count} jobs
            </Badge>
          </div>
        </div>

        <p className="text-xs text-muted-foreground font-mono truncate">
          {agentType.image}
        </p>

        {agentType.capabilities.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {agentType.capabilities.map((cap) => (
              <Badge key={cap} variant="secondary" className="text-xs">
                {cap}
              </Badge>
            ))}
          </div>
        )}

        {agentType.capabilities.length === 0 && (
          <p className="text-xs text-muted-foreground italic">No capabilities defined</p>
        )}
      </CardContent>
    </Card>
  );
}

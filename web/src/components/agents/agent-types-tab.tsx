"use client";

import { useState } from "react";
import { useAgentTypes } from "@/lib/hooks";
import { AgentTypeCard } from "./agent-type-card";
import { AgentTypeDetail } from "./agent-type-detail";

export function AgentTypesTab() {
  const { data: agentTypes, isLoading, isError } = useAgentTypes();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading agent types...</p>;
  }

  if (isError) {
    return <p className="text-sm text-red-400">Failed to load agent types.</p>;
  }

  if (!agentTypes || agentTypes.length === 0) {
    return <p className="text-sm text-muted-foreground">No agent types registered.</p>;
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {agentTypes.map((at) => (
        <AgentTypeCard
          key={at.id}
          agentType={at}
          isExpanded={expandedId === at.id}
          onToggle={() => setExpandedId(expandedId === at.id ? null : at.id)}
        />
      ))}
      {expandedId && agentTypes.find((at) => at.id === expandedId) && (
        <AgentTypeDetail
          agentType={agentTypes.find((at) => at.id === expandedId)!}
        />
      )}
    </div>
  );
}

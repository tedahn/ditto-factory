"use client";

import { use } from "react";
import { Header } from "@/components/layout/header";
import { AgentDetail } from "@/components/agents/agent-detail";

export default function AgentDetailPage({
  params,
}: {
  params: Promise<{ threadId: string }>;
}) {
  const { threadId } = use(params);

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6">
        <AgentDetail threadId={threadId} />
      </div>
    </div>
  );
}
